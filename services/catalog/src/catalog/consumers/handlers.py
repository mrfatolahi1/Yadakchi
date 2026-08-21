"""Event handlers — one per consumed topic.

**This module is where duplicate delivery is stopped.** Kafka is
at-least-once by design and the brief calls a non-idempotent consumer the
most common failure in this system, so the guard is explicit rather than
implied, and it is two guards rather than one:

``guard_duplicate``
    An exact redelivery of the same ``event_id`` on the same topic. The
    unique constraint on ``ProcessedEvent`` makes the second attempt a no-op.

``is_stale``
    A *different* event carrying older facts than we already applied —
    out-of-order across partitions, or a replay landing behind live traffic.
    Each read model remembers the ``occurred_at`` it was built from and
    refuses anything older.

The two are independent: the first stops the same message twice, the second
stops the wrong message winning. Both are needed, and both are tested.

Every handler is a plain function over a parsed envelope, so the whole
consumer path is testable without a broker.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from django.db import IntegrityError, transaction
from django.db.models import Q

from catalog import trust
from catalog.events import (
    ClicksRecordedPayload,
    ClustersChangedPayload,
    CrossRefsChangedPayload,
    Envelope,
    OffersEnrichedPayload,
    OffersFittedPayload,
    VehiclesChangedPayload,
)
from catalog.models import (
    ClickCounter,
    CrossRefReadModel,
    FitmentReadModel,
    OfferReadModel,
    ProcessedEvent,
    Product,
    ProductOffer,
    ProductSlug,
    Seller,
    VehicleReadModel,
)
from catalog.rebuild import rebuild_product, record_price_observation

logger = logging.getLogger("catalog.consumers")


# =================================================================== guards
def guard_duplicate(envelope: Envelope, topic: str, entity_key: str = "") -> bool:
    """Record this event, or report that we have already handled it.

    Returns ``True`` when the event is new and should be processed, ``False``
    when it is a redelivery. The write and the handler's work share one
    transaction, so a crash between them replays cleanly.
    """
    try:
        with transaction.atomic():
            ProcessedEvent.objects.create(
                topic=topic,
                event_id=envelope.event_id,
                entity_key=entity_key,
                occurred_at=envelope.occurred_at,
            )
    except IntegrityError:
        logger.info(
            "duplicate delivery ignored",
            extra={
                "topic": topic,
                "event_id": str(envelope.event_id),
                "entity_key": entity_key,
                "trace_id": envelope.trace_id,
            },
        )
        return False
    return True


def is_stale(existing_occurred_at: dt.datetime | None, incoming: dt.datetime) -> bool:
    """Would applying this event move the entity backwards in time?

    Delivery is only ordered *within* a partition, so comparing the fact's
    own timestamp is the only safe test. Equal timestamps are treated as
    stale: the same fact, already applied.
    """
    return existing_occurred_at is not None and incoming <= existing_occurred_at


def _rebuild_all(product_uids: Iterable[uuid.UUID], now: dt.datetime) -> None:
    for product_uid in sorted(set(product_uids), key=str):
        rebuild_product(product_uid, now)


def _products_for_offer(offer_uid: str) -> list[uuid.UUID]:
    """Every product this offer is a member of, in a stable order."""
    return sorted(
        ProductOffer.objects.filter(offer_uid=offer_uid).values_list("product_id", flat=True),
        key=str,
    )


# ============================================================ offers.enriched
def _ensure_seller(payload: OffersEnrichedPayload, now: dt.datetime) -> Seller:
    """Seller identity is catalog's own.

    No topic carries a seller's display name, domain or panel membership, so
    a seller is provisioned the first time an offer reveals the key: the name
    starts as the key and the domain is read off the listing URL, both of
    them corrected by a human in the admin later. A new seller starts in tier
    ``new`` with the capped ceiling — visibility is earned.
    """
    seller, created = Seller.objects.get_or_create(
        seller_key=payload.seller_key,
        defaults={
            "name": payload.seller_key,
            "domain": (urlparse(payload.url).hostname or "").removeprefix("www."),
            "source_key": payload.source_key,
            "first_seen_at": now,
            "updated_at": now,
        },
    )
    if created:
        trust.apply_trust(seller, now)
        seller.save()
        logger.info("seller provisioned", extra={"seller_key": seller.seller_key})
        return seller

    dirty = False
    if not seller.domain:
        seller.domain = (urlparse(payload.url).hostname or "").removeprefix("www.")
        dirty = True
    if seller.source_key is None and payload.source_key:
        seller.source_key = payload.source_key
        dirty = True
    if dirty:
        seller.updated_at = now
        seller.save(update_fields=["domain", "source_key", "updated_at"])
    return seller


@transaction.atomic
def handle_offer_enriched(envelope: Envelope, topic: str, key: str | None = None) -> bool:
    """Apply one enriched offer: read model, trust observation, price history."""
    payload = OffersEnrichedPayload.model_validate(envelope.payload)
    if not guard_duplicate(envelope, topic, payload.offer_uid):
        return False

    existing = OfferReadModel.objects.filter(offer_uid=payload.offer_uid).first()
    if existing and is_stale(existing.source_occurred_at, envelope.occurred_at):
        logger.info(
            "stale offer event ignored",
            extra={"offer_uid": payload.offer_uid, "trace_id": envelope.trace_id},
        )
        return False

    now = envelope.occurred_at
    seller = _ensure_seller(payload, now)

    # A re-observation of a known offer is the raw material of trust: did the
    # advertised price hold, was "in stock" still true? Only a genuinely new
    # event gets here, so nothing is counted twice.
    if existing:
        trust.observe_offer_change(seller, existing, payload.price_toman, payload.stock_status)
        seller.updated_at = now
        seller.save(
            update_fields=[
                "price_observations",
                "price_hits",
                "stock_observations",
                "stock_hits",
                "updated_at",
            ]
        )

    product_uids = _products_for_offer(payload.offer_uid)
    record_price_observation(
        offer_uid=payload.offer_uid,
        product_uid=product_uids[0] if product_uids else None,
        observed_at=payload.last_seen_at,
        price_toman=payload.price_toman,
        stock_status=payload.stock_status,
        previous=(existing.price_toman, existing.stock_status) if existing else None,
    )

    OfferReadModel.objects.update_or_create(
        offer_uid=payload.offer_uid,
        defaults={
            "source_key": payload.source_key,
            "external_key": payload.external_key,
            "seller_key": payload.seller_key,
            "url": payload.url,
            "raw_title": payload.raw_title,
            "title_normalized": payload.title_normalized,
            "brand": payload.brand,
            "part_number": payload.part_number,
            "part_type": payload.part_type,
            "authenticity_claim": payload.authenticity_claim,
            "pack_quantity": payload.pack_quantity,
            "price_toman": payload.price_toman,
            "stock_status": payload.stock_status,
            "image_url": payload.image_url,
            "vehicle_hints": payload.vehicle_hints,
            "vehicle_hints_excluded": payload.vehicle_hints_excluded,
            "overbroad_claim": payload.overbroad_claim,
            "confidences": payload.confidences,
            "extraction_provenance": payload.extraction_provenance,
            "normalizer_version": payload.normalizer_version,
            "first_seen_at": payload.first_seen_at,
            "last_seen_at": payload.last_seen_at,
            "is_active": payload.is_active,
            "source_occurred_at": envelope.occurred_at,
        },
    )

    _rebuild_all(product_uids, now)
    return True


# =========================================================== clusters.changed
@transaction.atomic
def handle_cluster_changed(envelope: Envelope, topic: str, key: str | None = None) -> bool:
    """Adopt a cluster as a product: membership, retirement, successor."""
    payload = ClustersChangedPayload.model_validate(envelope.payload)
    if not guard_duplicate(envelope, topic, str(payload.cluster_uid)):
        return False

    now = envelope.occurred_at
    # product_uid IS cluster_uid, adopted unchanged and never reassigned.
    product, created = Product.objects.get_or_create(
        product_uid=payload.cluster_uid,
        defaults={
            "slug": str(payload.cluster_uid),
            "title": "",
            "updated_at": now,
            "source_occurred_at": now,
        },
    )
    if created:
        ProductSlug.objects.get_or_create(
            slug=product.slug, defaults={"product": product, "is_current": True, "created_at": now}
        )
    elif is_stale(product.source_occurred_at, envelope.occurred_at):
        logger.info(
            "stale cluster event ignored",
            extra={"product_uid": str(payload.cluster_uid), "trace_id": envelope.trace_id},
        )
        return False

    # Members arrive as full state, so the local set is replaced, not merged.
    incoming = {m.offer_uid: m for m in payload.members}
    ProductOffer.objects.filter(product=product).exclude(offer_uid__in=incoming).delete()
    for offer_uid, member in incoming.items():
        ProductOffer.objects.update_or_create(
            product=product,
            offer_uid=offer_uid,
            defaults={
                "membership_confidence": member.confidence,
                "membership_provenance": member.provenance,
            },
        )

    product.cluster_change_reason = payload.change_reason
    product.cluster_computed_at = payload.computed_at
    product.source_occurred_at = envelope.occurred_at

    # A product is never deleted. It retires and points at what replaced it,
    # which is what turns a split into a 301 instead of a dead URL.
    if payload.successor_uid:
        product.successor_product_uid = payload.successor_uid
        product.retired_at = product.retired_at or now
        successor = Product.objects.filter(product_uid=payload.successor_uid).first()
        product.successor_slug = successor.slug if successor else None
    product.save()

    touched = [product.product_uid]
    touched.extend(_retire_predecessors(payload, product, now))
    _rebuild_all(touched, now)
    _refresh_successor_slugs(product.product_uid)
    return True


def _retire_predecessors(
    payload: ClustersChangedPayload, product: Product, now: dt.datetime
) -> list[uuid.UUID]:
    """Point a superseded cluster at the one that replaced it.

    ``successor_uid`` on the retiring cluster is the authoritative signal;
    this is the reverse index, and it only fires for a predecessor that has
    actually been emptied out and has no successor yet. Splits are frequent
    with aggressive merging, and every predecessor left dangling is a URL
    that stops resolving.
    """
    retired: list[uuid.UUID] = []
    for predecessor_uid in payload.predecessor_uids:
        if predecessor_uid == product.product_uid:
            continue
        predecessor = Product.objects.filter(product_uid=predecessor_uid).first()
        if predecessor is None or predecessor.successor_product_uid is not None:
            continue
        if ProductOffer.objects.filter(product=predecessor).exists():
            continue  # still has members of its own; not superseded yet
        predecessor.successor_product_uid = product.product_uid
        predecessor.successor_slug = product.slug
        predecessor.retired_at = predecessor.retired_at or now
        predecessor.save(update_fields=["successor_product_uid", "successor_slug", "retired_at"])
        retired.append(predecessor.product_uid)
    return retired


def _refresh_successor_slugs(successor_uid: uuid.UUID) -> None:
    """Keep the denormalised redirect target current.

    The successor's slug can change after the predecessors point at it, and a
    redirect to a slug that no longer resolves is no better than no redirect.
    """
    successor = Product.objects.filter(product_uid=successor_uid).only("slug").first()
    if successor is None:
        return
    Product.objects.filter(successor_product_uid=successor_uid).exclude(
        successor_slug=successor.slug
    ).update(successor_slug=successor.slug)


# ============================================================== offers.fitted
@transaction.atomic
def handle_offer_fitted(envelope: Envelope, topic: str, key: str | None = None) -> bool:
    payload = OffersFittedPayload.model_validate(envelope.payload)
    if not guard_duplicate(envelope, topic, payload.offer_uid):
        return False

    existing = FitmentReadModel.objects.filter(offer_uid=payload.offer_uid).first()
    if existing and is_stale(existing.source_occurred_at, envelope.occurred_at):
        return False

    FitmentReadModel.objects.update_or_create(
        offer_uid=payload.offer_uid,
        defaults={
            "fitments": [f.model_dump() for f in payload.fitments],
            "crossref_codes": list(payload.crossref_codes),
            "risky_family": payload.risky_family.model_dump() if payload.risky_family else None,
            "computed_at": payload.computed_at,
            "source_occurred_at": envelope.occurred_at,
        },
    )
    _rebuild_all(_products_for_offer(payload.offer_uid), envelope.occurred_at)
    return True


# ============================================================ vehicles.changed
@transaction.atomic
def handle_vehicle_changed(envelope: Envelope, topic: str, key: str | None = None) -> bool:
    """Compacted topic: a null payload is a tombstone, not a malformed event."""
    if envelope.is_tombstone:
        return _handle_vehicle_tombstone(envelope, topic, key)

    payload = VehiclesChangedPayload.model_validate(envelope.payload)
    if not guard_duplicate(envelope, topic, payload.vehicle_slug):
        return False

    existing = VehicleReadModel.objects.filter(vehicle_slug=payload.vehicle_slug).first()
    if existing and is_stale(existing.source_occurred_at, envelope.occurred_at):
        return False

    VehicleReadModel.objects.update_or_create(
        vehicle_slug=payload.vehicle_slug,
        defaults={
            "brand": payload.brand,
            "model": payload.model,
            "trim": payload.trim,
            "year_from": payload.year_from,
            "year_to": payload.year_to,
            "engine_code": payload.engine_code,
            "display_name_fa": payload.display_name_fa,
            "aliases": list(payload.aliases),
            "is_published": payload.is_published,
            "updated_at": payload.updated_at,
            "is_deleted": False,
            "source_occurred_at": envelope.occurred_at,
        },
    )
    _rebuild_all(_products_referencing_vehicle(payload.vehicle_slug), envelope.occurred_at)
    return True


def _handle_vehicle_tombstone(envelope: Envelope, topic: str, key: str | None) -> bool:
    """A deleted vehicle.

    The identity lives in the Kafka message key — a tombstone has no payload
    to read it from. The row is kept and flagged rather than removed, so the
    history stays explicable, and every product that referenced the vehicle
    is rebuilt without it.
    """
    if not key:
        logger.warning(
            "vehicle tombstone with no message key ignored",
            extra={"trace_id": envelope.trace_id, "event_id": str(envelope.event_id)},
        )
        return False
    if not guard_duplicate(envelope, topic, key):
        return False

    affected = _products_referencing_vehicle(key)
    VehicleReadModel.objects.filter(vehicle_slug=key).update(
        is_deleted=True, source_occurred_at=envelope.occurred_at
    )
    _rebuild_all(affected, envelope.occurred_at)
    logger.info("vehicle tombstoned", extra={"vehicle_slug": key, "trace_id": envelope.trace_id})
    return True


def _products_referencing_vehicle(vehicle_slug: str) -> list[uuid.UUID]:
    return list(
        Product.objects.filter(
            Q(vehicles_compatible__contains=[vehicle_slug])
            | Q(vehicles_incompatible__contains=[vehicle_slug])
            | Q(vehicles_unknown__contains=[vehicle_slug])
        ).values_list("product_uid", flat=True)
    )


# =========================================================== crossrefs.changed
@transaction.atomic
def handle_crossref_changed(envelope: Envelope, topic: str, key: str | None = None) -> bool:
    if envelope.is_tombstone:
        return _handle_crossref_tombstone(envelope, topic, key)

    payload = CrossRefsChangedPayload.model_validate(envelope.payload)
    pair_key = f"{payload.code_a}|{payload.code_b}"
    if not guard_duplicate(envelope, topic, pair_key):
        return False

    existing = CrossRefReadModel.objects.filter(pair_key=pair_key).first()
    if existing and is_stale(existing.source_occurred_at, envelope.occurred_at):
        return False

    CrossRefReadModel.objects.update_or_create(
        pair_key=pair_key,
        defaults={
            "code_a": payload.code_a,
            "code_b": payload.code_b,
            "brand_a": payload.brand_a,
            "brand_b": payload.brand_b,
            "confidence": payload.confidence,
            "provenance": payload.provenance,
            "updated_at": payload.updated_at,
            "is_deleted": False,
            "source_occurred_at": envelope.occurred_at,
        },
    )

    codes = [payload.code_a, payload.code_b]
    affected = Product.objects.filter(
        Q(part_numbers__overlap=codes) | Q(crossref_codes__overlap=codes)
    ).values_list("product_uid", flat=True)
    _rebuild_all(list(affected), envelope.occurred_at)
    return True


def _handle_crossref_tombstone(envelope: Envelope, topic: str, key: str | None) -> bool:
    """A withdrawn cross-reference pair, identified by the message key
    ``{code_a}|{code_b}``. Flagged rather than deleted, and every product that
    was showing the equivalence is rebuilt without it."""
    if not key:
        logger.warning(
            "crossref tombstone with no message key ignored",
            extra={"trace_id": envelope.trace_id, "event_id": str(envelope.event_id)},
        )
        return False
    if not guard_duplicate(envelope, topic, key):
        return False

    CrossRefReadModel.objects.filter(pair_key=key).update(
        is_deleted=True, source_occurred_at=envelope.occurred_at
    )
    codes = key.split("|")
    affected = Product.objects.filter(
        Q(part_numbers__overlap=codes) | Q(crossref_codes__overlap=codes)
    ).values_list("product_uid", flat=True)
    _rebuild_all(list(affected), envelope.occurred_at)
    return True


# ============================================================ clicks.recorded
@transaction.atomic
def handle_click_recorded(envelope: Envelope, topic: str, key: str | None = None) -> bool:
    """Traffic, and nothing more.

    Clicks order related products and give ops a popularity signal. They do
    **not** touch ranking and they are **not** in the published payload:
    sellers pay per click, and a click that could buy a better position would
    turn the comparison into an auction. They also do not mark the product
    dirty — a click changes nothing a consumer renders.
    """
    payload = ClicksRecordedPayload.model_validate(envelope.payload)
    if not guard_duplicate(envelope, topic, payload.click_id):
        return False

    counter, _ = ClickCounter.objects.get_or_create(
        product_uid=payload.product_uid,
        offer_uid=payload.offer_uid,
        defaults={"seller_key": payload.seller_key},
    )
    counter.clicks += 1
    if payload.is_suspicious:
        counter.suspicious_clicks += 1
    counter.last_click_at = payload.occurred_at
    counter.save(update_fields=["clicks", "suspicious_clicks", "last_click_at"])

    Product.objects.filter(product_uid=payload.product_uid).update(
        click_count=_click_total(payload.product_uid)
    )
    return True


def _click_total(product_uid: uuid.UUID) -> int:
    from django.db.models import Sum

    total = ClickCounter.objects.filter(product_uid=product_uid).aggregate(total=Sum("clicks"))[
        "total"
    ]
    return int(total or 0)


HANDLERS: dict[str, Any] = {
    "offers.enriched": handle_offer_enriched,
    "clusters.changed": handle_cluster_changed,
    "offers.fitted": handle_offer_fitted,
    "vehicles.changed": handle_vehicle_changed,
    "crossrefs.changed": handle_crossref_changed,
    "clicks.recorded": handle_click_recorded,
}
