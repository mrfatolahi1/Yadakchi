"""What we put on the wire must match what we published.

`search`, `ops`, `web` and `billing` hold byte-identical copies of the two
schemas in contracts/published/. These tests validate real emitted events
against those files, so a payload cannot drift away from its contract
without the build failing.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from catalog import producer
from catalog.models import Seller
from catalog.tasks import emit_seller, flush_pending_products
from tests.conftest import (
    NOW,
    Pipeline,
    cluster_payload,
    fitment_payload,
    offer_payload,
)

pytestmark = pytest.mark.django_db

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
CLUSTER = uuid.UUID("93c9da93-7ffb-498e-afc1-2798ea05112e")


def validator(topic: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "published" / f"{topic}.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def test_emitted_products_changed_matches_its_published_schema(
    pipeline: Pipeline, seeded_vehicles: None, transport: producer.MemoryTransport
) -> None:
    for seed, seller, price, stock in (
        ("a", "yadakyar", 2_450_000, "in_stock"),
        ("b", "yadaksara", 2_380_000, "out_of_stock"),
        ("c", "otoyar", 2_690_000, "in_stock"),
    ):
        pipeline.feed(
            "offers.enriched",
            offer_payload(seed, seller_key=seller, price_toman=price, stock_status=stock),
        )
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a", "b", "c"]))
    for seed in ("a", "b", "c"):
        pipeline.feed("offers.fitted", fitment_payload(seed))

    assert flush_pending_products(NOW + dt.timedelta(minutes=5)) == 1
    events = transport.for_topic("yadakchi.products.changed.v1")
    assert len(events) == 1

    event = events[0]
    assert event.value is not None
    validator("yadakchi.products.changed.v1").validate(event.value)

    # The topic is compacted and keyed by identity.
    assert event.key == str(CLUSTER)
    assert event.value["producer"] == "catalog"
    assert event.value["event_type"] == "products.changed"
    assert event.value["version"] == 1


def test_emitted_sellers_changed_matches_its_published_schema(
    pipeline: Pipeline, transport: producer.MemoryTransport
) -> None:
    pipeline.feed("offers.enriched", offer_payload("a"))
    seller = Seller.objects.get(seller_key="yadakyar")
    assert emit_seller(seller, NOW) is True

    events = transport.for_topic("yadakchi.sellers.changed.v1")
    assert len(events) == 1
    assert events[0].value is not None
    validator("yadakchi.sellers.changed.v1").validate(events[0].value)
    assert events[0].key == "yadakyar"


def test_the_payload_carries_exactly_the_declared_fields(
    pipeline: Pipeline, seeded_vehicles: None, transport: producer.MemoryTransport
) -> None:
    """A published shape is not changed without a version bump.

    Related parts and seller badges are useful, and they are deliberately
    *not* here: they live on the read API instead, because the contract has
    no field for them.
    """
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))
    pipeline.feed("offers.fitted", fitment_payload("a"))
    flush_pending_products(NOW + dt.timedelta(minutes=5))

    emitted = transport.for_topic("yadakchi.products.changed.v1")[0].value
    assert emitted is not None
    payload = emitted["payload"]
    schema = json.loads(
        (CONTRACTS / "published" / "yadakchi.products.changed.v1.json").read_text(encoding="utf-8")
    )
    declared = set(schema["properties"]["payload"]["properties"])
    assert set(payload) == declared

    offer_declared = set(
        schema["properties"]["payload"]["properties"]["offers"]["items"]["properties"]
    )
    assert set(payload["offers"][0]) == offer_declared
    assert "related_products" not in payload
    assert "seller_badges" not in payload


@pytest.mark.parametrize("topic", ["yadakchi.products.changed.v1", "yadakchi.sellers.changed.v1"])
def test_the_shipped_examples_validate_against_their_schema(topic: str) -> None:
    """The example payloads are used as fixtures elsewhere in the system;
    if one drifts from the schema, catch it here."""
    check = validator(topic)
    examples = sorted((CONTRACTS / "examples" / topic).glob("*.json"))
    assert examples, f"no examples for {topic}"
    for path in examples:
        check.validate(json.loads(path.read_text(encoding="utf-8")))


def test_consumed_schemas_are_present_for_every_topic_we_read() -> None:
    """Every edge in SPEC.md's "How it connects" table has a contract on disk."""
    expected = {
        "yadakchi.clusters.changed.v1",
        "yadakchi.offers.enriched.v1",
        "yadakchi.offers.fitted.v1",
        "yadakchi.vehicles.changed.v1",
        "yadakchi.crossrefs.changed.v1",
        "yadakchi.clicks.recorded.v1",
    }
    on_disk = {p.stem for p in (CONTRACTS / "consumed").glob("*.json")}
    assert expected <= on_disk


def test_a_tombstone_parses_as_an_envelope_with_no_payload() -> None:
    """Compacted topics carry deletions as `payload: null`. That is a valid
    message, not a malformed one."""
    from catalog.events import Envelope

    envelope = Envelope.model_validate(
        {
            "event_id": str(uuid.uuid4()),
            "event_type": "vehicles.changed",
            "version": 1,
            "occurred_at": "2026-08-19T07:00:00Z",
            "producer": "fitment",
            "trace_id": "abc123",
            "payload": None,
        }
    )
    assert envelope.is_tombstone


def test_unknown_fields_in_a_consumed_event_are_ignored_not_rejected(
    pipeline: Pipeline,
) -> None:
    """A producer adding an optional field must not break us. That tolerance
    is the whole reason the schemas allow additional properties."""
    payload: dict[str, Any] = offer_payload("a")
    payload["a_field_from_the_future"] = {"nested": True}
    assert pipeline.feed("offers.enriched", payload) is True


def test_a_newly_provisioned_seller_reaches_billing_without_waiting_for_trust(
    pipeline: Pipeline, transport: producer.MemoryTransport
) -> None:
    """billing keeps its seller read model from this topic and never calls
    us, so a seller we have only just met has to be published promptly."""
    from catalog.tasks import flush_pending_sellers

    pipeline.feed("offers.enriched", offer_payload("a", seller_key="brandnew"))
    assert flush_pending_sellers(NOW) == 1

    events = transport.for_topic("yadakchi.sellers.changed.v1")
    assert [e.key for e in events] == ["brandnew"]
    assert events[0].value is not None
    validator("yadakchi.sellers.changed.v1").validate(events[0].value)
    assert events[0].value["payload"]["tier"] == "new"

    # Nothing changed since: no second event.
    transport.clear()
    assert flush_pending_sellers(NOW) == 0
    assert transport.for_topic("yadakchi.sellers.changed.v1") == []
