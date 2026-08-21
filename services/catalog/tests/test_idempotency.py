"""Acceptance criteria 9 and 10, and the duplicate-delivery guarantee.

Kafka is at-least-once. The brief calls a non-idempotent consumer the most
common failure in this system, so the guards are tested directly rather than
inferred from behaviour: the same message twice, an older message after a
newer one, and a full replay of every topic into an empty database.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from catalog import producer
from catalog.models import (
    OfferReadModel,
    PriceHistory,
    ProcessedEvent,
    Product,
    ProductOffer,
    Seller,
)
from catalog.rebuild import rebuild_product
from catalog.tasks import flush_pending_products
from tests.conftest import (
    NOW,
    Pipeline,
    cluster_payload,
    fitment_payload,
    make_envelope,
    offer_payload,
    offer_uid,
    vehicle_payload,
)

pytestmark = pytest.mark.django_db

CLUSTER = uuid.UUID("93c9da93-7ffb-498e-afc1-2798ea05112e")

#: (event_type, payload, kafka message key)
EventRow = tuple[str, dict[str, Any], str | None]


# ======================================================= duplicate delivery
def test_a_duplicated_offer_event_is_a_no_op(pipeline: Pipeline) -> None:
    """The same message, delivered twice. The second must change nothing —
    not the read model, not the trust counters, not the price history."""
    payload = offer_payload("a")
    first, second = pipeline.deliver_twice("offers.enriched", payload)

    assert first is True
    assert second is False
    assert OfferReadModel.objects.count() == 1
    assert PriceHistory.objects.count() == 1
    assert ProcessedEvent.objects.count() == 1

    seller = Seller.objects.get(seller_key="yadakyar")
    assert seller.price_observations == 0  # first sight is not a re-observation


def test_a_duplicated_cluster_event_does_not_duplicate_members(
    pipeline: Pipeline,
) -> None:
    for seed in ("a", "b"):
        pipeline.feed("offers.enriched", offer_payload(seed))
    first, second = pipeline.deliver_twice("clusters.changed", cluster_payload(CLUSTER, ["a", "b"]))

    assert (first, second) == (True, False)
    assert ProductOffer.objects.filter(product_id=CLUSTER).count() == 2
    assert Product.objects.count() == 1


def test_a_duplicated_click_is_counted_once(pipeline: Pipeline) -> None:
    """billing charges idempotently on click_id; so do we."""
    for seed in ("a",):
        pipeline.feed("offers.enriched", offer_payload(seed))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))

    payload = {
        "click_id": "click-1",
        "product_uid": str(CLUSTER),
        "offer_uid": offer_uid("a"),
        "seller_key": "yadakyar",
        "cost_toman": 1500,
        "is_suspicious": False,
        "occurred_at": "2026-08-19T07:00:00Z",
    }
    first, second = pipeline.deliver_twice("clicks.recorded", payload)

    assert (first, second) == (True, False)
    Product.objects.get(product_uid=CLUSTER).refresh_from_db()
    assert Product.objects.get(product_uid=CLUSTER).click_count == 1


def test_trust_counters_are_not_inflated_by_redelivery(pipeline: Pipeline) -> None:
    """A re-observation counts once. Counting a redelivery would let a
    seller's accuracy drift purely from broker behaviour."""
    pipeline.feed("offers.enriched", offer_payload("a"))

    later = offer_payload("a", price_toman=2_500_000)
    envelope = make_envelope("offers.enriched", later, occurred_at=NOW + dt.timedelta(hours=1))
    pipeline.feed("offers.enriched", later, envelope=envelope)
    pipeline.feed("offers.enriched", later, envelope=envelope)  # redelivered

    seller = Seller.objects.get(seller_key="yadakyar")
    assert seller.price_observations == 1
    assert seller.price_hits == 0  # the price moved


# ============================================================ stale delivery
def test_an_older_event_never_overwrites_a_newer_one(pipeline: Pipeline) -> None:
    """Ordering holds only within a partition, so a stale fact has to be
    rejected on its own timestamp."""
    pipeline.feed(
        "offers.enriched",
        offer_payload("a", price_toman=3_000_000),
        occurred_at=NOW + dt.timedelta(hours=2),
    )
    applied = pipeline.feed(
        "offers.enriched",
        offer_payload("a", price_toman=1_000_000),
        occurred_at=NOW,  # older fact, delivered later
    )

    assert applied is False
    assert OfferReadModel.objects.get(offer_uid=offer_uid("a")).price_toman == 3_000_000


def test_a_stale_cluster_event_is_ignored(pipeline: Pipeline) -> None:
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("offers.enriched", offer_payload("b"))
    pipeline.feed(
        "clusters.changed",
        cluster_payload(CLUSTER, ["a", "b"]),
        occurred_at=NOW + dt.timedelta(hours=1),
    )
    applied = pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]), occurred_at=NOW)

    assert applied is False
    assert ProductOffer.objects.filter(product_id=CLUSTER).count() == 2


# ============================ criterion 9: one event per change, debounced
def test_five_changes_in_a_minute_emit_one_event(
    pipeline: Pipeline, seeded_vehicles: None, transport: producer.MemoryTransport
) -> None:
    """Acceptance criterion 9, stated exactly."""
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))
    pipeline.feed("offers.fitted", fitment_payload("a"))

    for index in range(5):
        pipeline.feed(
            "offers.enriched",
            offer_payload("a", price_toman=2_000_000 + index * 1000),
            occurred_at=NOW + dt.timedelta(seconds=index + 1),
        )

    transport.clear()
    # The window has not closed yet: nothing goes out.
    assert flush_pending_products(NOW + dt.timedelta(seconds=10)) == 0
    assert transport.for_topic("yadakchi.products.changed.v1") == []

    # Once it closes, the whole burst is one event.
    assert flush_pending_products(NOW + dt.timedelta(minutes=5)) == 1
    assert len(transport.for_topic("yadakchi.products.changed.v1")) == 1

    # And nothing more, because nothing else changed.
    assert flush_pending_products(NOW + dt.timedelta(minutes=10)) == 0
    assert len(transport.for_topic("yadakchi.products.changed.v1")) == 1


def test_rebuilding_twice_produces_identical_rows_and_no_second_event(
    pipeline: Pipeline, seeded_vehicles: None, transport: producer.MemoryTransport
) -> None:
    """ "Rebuilding the same product twice must produce identical rows and at
    most one debounced event." Timestamps included — an unchanged rebuild
    must not even re-stamp updated_at."""
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))
    pipeline.feed("offers.fitted", fitment_payload("a"))
    flush_pending_products(NOW + dt.timedelta(minutes=5))
    transport.clear()

    before = Product.objects.get(product_uid=CLUSTER)
    snapshot = (before.document, before.document_hash, before.updated_at, before.slug)

    result = rebuild_product(CLUSTER, NOW + dt.timedelta(hours=3))
    assert result is not None and result.changed is False

    after = Product.objects.get(product_uid=CLUSTER)
    assert (after.document, after.document_hash, after.updated_at, after.slug) == snapshot

    assert flush_pending_products(NOW + dt.timedelta(hours=4)) == 0
    assert transport.for_topic("yadakchi.products.changed.v1") == []


def test_price_history_is_appended_on_change_only(pipeline: Pipeline) -> None:
    """ "Append on actual change only, never per event"."""
    pipeline.feed("offers.enriched", offer_payload("a"))
    assert PriceHistory.objects.count() == 1

    # A different event for the same offer, same price and stock: no new point.
    pipeline.feed(
        "offers.enriched",
        offer_payload("a", title_normalized="لنت ترمز جلو پژو 206 عظام"),
        occurred_at=NOW + dt.timedelta(hours=1),
    )
    assert PriceHistory.objects.count() == 1

    # A real price move: one new point.
    pipeline.feed(
        "offers.enriched",
        offer_payload(
            "a",
            price_toman=2_600_000,
            last_seen_at="2026-08-20T07:00:00Z",
        ),
        occurred_at=NOW + dt.timedelta(days=1),
    )
    assert PriceHistory.objects.count() == 2


# ================================= criterion 10: replay rebuilds the world
def _full_event_log() -> list[EventRow]:
    """One of everything, in the order a real pipeline would emit it."""
    log: list[EventRow] = []
    for slug in ("peugeot-206-type-5", "peugeot-206-type-6", "peugeot-405-glx"):
        log.append(("vehicles.changed", vehicle_payload(slug), slug))
    log.append(
        (
            "crossrefs.changed",
            {
                "code_a": "425235",
                "code_b": "425438",
                "brand_a": "bosch",
                "brand_b": "ezam",
                "confidence": 0.9,
                "provenance": "rule",
                "updated_at": "2026-08-18T00:00:00Z",
            },
            "425235|425438",
        )
    )
    for seed, seller, price in (
        ("a", "yadakyar", 2_450_000),
        ("b", "yadaksara", 2_380_000),
        ("c", "otoyar", 2_690_000),
    ):
        log.append(
            ("offers.enriched", offer_payload(seed, seller_key=seller, price_toman=price), None)
        )
    log.append(("clusters.changed", cluster_payload(CLUSTER, ["a", "b", "c"]), str(CLUSTER)))
    for seed in ("a", "b", "c"):
        log.append(("offers.fitted", fitment_payload(seed), None))
    log.append(
        (
            "clicks.recorded",
            {
                "click_id": "click-1",
                "product_uid": str(CLUSTER),
                "offer_uid": offer_uid("a"),
                "seller_key": "yadakyar",
                "cost_toman": 1500,
                "is_suspicious": False,
                "occurred_at": "2026-08-19T07:30:00Z",
            },
            str(CLUSTER),
        )
    )
    return log


def _replay(pipeline: Pipeline, log: list[EventRow]) -> None:
    for event_type, payload, key in log:
        pipeline.feed(event_type, payload, key=key)


def test_replaying_every_topic_into_an_empty_database_reproduces_the_product(
    pipeline: Pipeline,
) -> None:
    """Acceptance criterion 10.

    The whole log is played once, the database is emptied, and the same log
    is played again. The resulting product must be identical — the same
    document, the same slug, the same ranked order, the same statistics.
    """
    log = _full_event_log()

    _replay(pipeline, log)
    first = Product.objects.get(product_uid=CLUSTER)
    first_document = first.document
    first_slug = first.slug

    # Empty the world, exactly as a rebuild-from-scratch would.
    from catalog.models import (
        ClickCounter,
        CrossRefReadModel,
        FitmentReadModel,
        ProductSlug,
        VehicleReadModel,
    )

    everything = (
        PriceHistory,
        ClickCounter,
        ProductOffer,
        ProductSlug,
        Product,
        OfferReadModel,
        FitmentReadModel,
        VehicleReadModel,
        CrossRefReadModel,
        Seller,
        ProcessedEvent,
    )
    for model in everything:
        model.objects.all().delete()

    _replay(pipeline, log)
    second = Product.objects.get(product_uid=CLUSTER)

    assert second.slug == first_slug
    assert second.document == first_document


def test_replaying_the_same_log_twice_without_clearing_changes_nothing(
    pipeline: Pipeline, transport: producer.MemoryTransport
) -> None:
    """A replay landing on top of live state is the ordinary reprocessing
    case, and it must be a no-op rather than a second set of events."""
    log = _full_event_log()
    _replay(pipeline, log)
    flush_pending_products(NOW + dt.timedelta(minutes=5))

    before = Product.objects.get(product_uid=CLUSTER)
    snapshot = (before.document, before.updated_at)
    transport.clear()

    _replay(pipeline, log)

    after = Product.objects.get(product_uid=CLUSTER)
    assert (after.document, after.updated_at) == snapshot
    assert flush_pending_products(NOW + dt.timedelta(minutes=10)) == 0
    assert transport.for_topic("yadakchi.products.changed.v1") == []
