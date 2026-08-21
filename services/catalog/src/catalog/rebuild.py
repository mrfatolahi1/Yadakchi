"""Assembling a product from its inputs.

This is where a match cluster becomes a page: a representative is elected, a
title and slug are derived, the seller list is denormalised and ranked, price
statistics and history are computed, fitment is aggregated across members,
and the publication gate is applied.

The whole function is deliberately pure-ish and total: given the same rows it
produces the same product, every time, which is what makes both "rebuilding
twice changes nothing" and "replaying the log reproduces the catalogue" true.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from catalog import pricing, ranking, related, representative
from catalog.events import iso_utc
from catalog.models import (
    CrossRefReadModel,
    FitmentReadModel,
    OfferReadModel,
    PriceHistory,
    Product,
    ProductOffer,
    ProductSlug,
    Seller,
    StockStatus,
    VehicleReadModel,
)
from catalog.slugs import build_slug
from catalog.titles import contains_promotional

logger = logging.getLogger("catalog.rebuild")

#: Used only when a cluster somehow has no usable text anywhere. A product
#: must never have an empty title — the schema forbids it and an empty
#: <title> is a broken page.
FALLBACK_TITLE = "قطعه یدکی"


@dataclass(frozen=True)
class RebuildResult:
    product: Product
    changed: bool
    document_hash: str


# ------------------------------------------------------------------ fitment
def aggregate_fitments(
    fitments_by_offer: dict[str, list[dict[str, Any]]], known_vehicles: set[str]
) -> tuple[list[str], list[str], list[str]]:
    """Roll per-offer verdicts up to the product, tri-state preserved.

    Members disagree, so each vehicle's verdicts are pooled by summing
    confidence per status and taking the strongest. A tie resolves to
    ``unknown`` — "show with a caveat" — because a confidently wrong fitment
    is worse than an honest shrug, and because `search` excludes only the
    explicit ``incompatible`` list.

    A vehicle we have never heard of on ``vehicles.changed`` is dropped: we
    would have nothing to render it with.
    """
    weights: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for verdicts in fitments_by_offer.values():
        for verdict in verdicts:
            slug = verdict.get("vehicle_slug")
            status = verdict.get("status")
            if not slug or status not in {"compatible", "incompatible", "unknown"}:
                continue
            if known_vehicles and slug not in known_vehicles:
                continue
            weights[slug][status] += float(verdict.get("confidence") or 0.0)

    compatible: list[str] = []
    incompatible: list[str] = []
    unknown: list[str] = []
    for slug in sorted(weights):
        by_status = weights[slug]
        best = max(by_status.values())
        winners = sorted(status for status, value in by_status.items() if value == best)
        status = winners[0] if len(winners) == 1 else "unknown"
        # Zero total confidence is no evidence at all.
        if best <= 0.0:
            status = "unknown"
        {"compatible": compatible, "incompatible": incompatible, "unknown": unknown}[status].append(
            slug
        )

    return compatible, incompatible, unknown


def expand_crossrefs(part_numbers: Iterable[str], seed_codes: Iterable[str]) -> list[str]:
    """Every equivalent code we can offer, from the members and the
    cross-reference table. A display hint only — never an identity claim, and
    never a reason to merge anything."""
    codes = {code for code in seed_codes if code}
    numbers = {n for n in part_numbers if n}
    if numbers:
        pairs = CrossRefReadModel.objects.filter(is_deleted=False).filter(
            Q(code_a__in=numbers) | Q(code_b__in=numbers)
        )
        for pair in pairs:
            codes.add(pair.code_b if pair.code_a in numbers else pair.code_a)
    return sorted(codes - numbers)


def pick_risky_note(fitment_rows: Sequence[FitmentReadModel]) -> str | None:
    """The Persian warning for a risky part family, if any member carries one.

    Deterministic: the most frequently seen note wins, ties break on the text
    itself, so a rebuild does not shuffle the warning.
    """
    notes: dict[str, int] = defaultdict(int)
    for row in fitment_rows:
        risky = row.risky_family
        if isinstance(risky, dict) and risky.get("note_fa"):
            notes[str(risky["note_fa"])] += 1
    if not notes:
        return None
    best = max(notes.values())
    return sorted(note for note, count in notes.items() if count == best)[0]


# ----------------------------------------------------------------- gate
def publication_gate(
    *,
    title: str,
    active_offers: int,
    has_price: bool,
    substance_facts: int,
    has_risky_family: bool,
    risky_note: str | None,
    has_explicit_verdict: bool,
    is_retired: bool,
) -> list[str]:
    """Why this product may not be published. Empty means publish.

    Part six's gate is "at least one active offer, a non-empty title and —
    for risky part families — an explicit verdict or the attached warning",
    plus the substance rule that the same section spells out: the indexing
    policy is index-everything, so a page with nothing on it costs the whole
    domain. A single-offer product is fine; a single offer with no price, no
    part number, no cross-reference and no fitment is a thin page.
    """
    reasons: list[str] = []
    if is_retired:
        reasons.append("retired")
    if active_offers < 1:
        reasons.append("no_active_offers")
    if not title.strip():
        reasons.append("empty_title")
    if contains_promotional(title):
        reasons.append("promotional_title")
    if settings.PUBLICATION_REQUIRE_PRICE and not has_price:
        reasons.append("no_usable_price")
    if substance_facts < settings.PUBLICATION_MIN_SUBSTANCE_FACTS:
        reasons.append("thin_content")
    if has_risky_family and not (risky_note or has_explicit_verdict):
        reasons.append("risky_family_without_verdict")
    return reasons


# ------------------------------------------------------------------ rebuild
def _seller_map(seller_keys: Iterable[str]) -> dict[str, Seller]:
    return {s.seller_key: s for s in Seller.objects.filter(seller_key__in=set(seller_keys))}


def _price_history_points(
    product_uid: uuid.UUID, member_uids: Sequence[str], *, since: dt.datetime
) -> list[pricing.HistoryPoint]:
    """History for this product's chart.

    Matched by *either* the offers that are members now or the product tag
    written when the row was recorded, so a retired product keeps the chart
    it earned and a fresh product inherits its members' full history.
    """
    rows = PriceHistory.objects.filter(
        Q(offer_uid__in=list(member_uids)) | Q(product_uid=product_uid)
    ).filter(observed_at__gte=since)
    return [
        pricing.HistoryPoint(r.offer_uid, r.observed_at, r.price_toman, r.stock_status)
        for r in rows.only("offer_uid", "observed_at", "price_toman", "stock_status")
    ]


def _assign_slug(product: Product, title: str, now: dt.datetime) -> str:
    """Derive the slug, and remember every slug this product has ever had."""
    slug = build_slug(title or FALLBACK_TITLE, product.product_uid)
    if slug == product.slug:
        return slug

    # A slug that has been out in the world keeps resolving, for ever.
    ProductSlug.objects.filter(product=product).update(is_current=False)
    ProductSlug.objects.update_or_create(
        slug=slug, defaults={"product": product, "is_current": True, "created_at": now}
    )
    return slug


def document_hash(document: dict[str, Any]) -> str:
    """A stable fingerprint of the payload.

    ``updated_at`` is excluded on purpose: it is a clock reading, not a fact
    about the product. Hashing it would make every rebuild look like a
    change, which would in turn re-stamp the row and re-emit the event —
    exactly what "rebuilding the same product twice produces identical rows
    and at most one event" forbids.
    """
    material = {k: v for k, v in document.items() if k != "updated_at"}
    canonical = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@transaction.atomic
def rebuild_product(product_uid: uuid.UUID, now: dt.datetime) -> RebuildResult | None:
    """Rebuild one product from its current inputs.

    Returns ``None`` when the product does not exist — a click or a fitment
    can arrive for a cluster we have not been told about yet, and inventing a
    product from it would be guessing.
    """
    try:
        product = Product.objects.select_for_update().get(product_uid=product_uid)
    except Product.DoesNotExist:
        return None

    previous_updated_at = product.updated_at
    memberships = list(ProductOffer.objects.filter(product=product))
    member_uids = [m.offer_uid for m in memberships]
    membership_by_uid = {m.offer_uid: m for m in memberships}

    offers = {o.offer_uid: o for o in OfferReadModel.objects.filter(offer_uid__in=member_uids)}
    active = sorted((o for o in offers.values() if o.is_active), key=lambda o: o.offer_uid)

    sellers = _seller_map(o.seller_key for o in active)
    trust_by_seller = {key: s.trust_score for key, s in sellers.items()}

    # --- representative, title, image -------------------------------------
    election = representative.elect(
        active, trust_by_seller, forced_offer_uid=product.representative_override
    )
    title = product.title_override or election.title or FALLBACK_TITLE
    image_url = product.image_url_override or election.image_url

    # --- ranked seller list ------------------------------------------------
    rankable = [
        ranking.RankableOffer(
            offer_uid=o.offer_uid,
            seller_key=o.seller_key,
            trust_score=trust_by_seller.get(o.seller_key, 0.0),
            price_toman=o.price_toman,
            stock_status=o.stock_status,
            last_seen_at=o.last_seen_at,
        )
        for o in active
    ]
    ranked = ranking.rank(rankable, now)
    ranked_by_uid = {r.offer_uid: r for r in ranked}

    priced = [pricing.PricedOffer(o.offer_uid, o.price_toman, o.stock_status) for o in active]
    stats = pricing.price_statistics(priced)
    ordered_for_badge = [next(p for p in priced if p.offer_uid == r.offer_uid) for r in ranked]
    cheapest_uid = pricing.choose_cheapest(ordered_for_badge)

    # --- fitment, part numbers, cross-references ---------------------------
    fitment_rows = list(FitmentReadModel.objects.filter(offer_uid__in=member_uids))
    known_vehicles = set(
        VehicleReadModel.objects.filter(is_deleted=False).values_list("vehicle_slug", flat=True)
    )
    compatible, incompatible, unknown = aggregate_fitments(
        {row.offer_uid: list(row.fitments or []) for row in fitment_rows}, known_vehicles
    )
    part_numbers = sorted({o.part_number for o in active if o.part_number})
    seed_codes = {code for row in fitment_rows for code in (row.crossref_codes or [])}
    crossref_codes = expand_crossrefs(part_numbers, seed_codes)
    risky_note = pick_risky_note(fitment_rows)

    # --- price history -----------------------------------------------------
    window_start = now - dt.timedelta(days=settings.PRICE_SERIES_WINDOW_DAYS)
    points = _price_history_points(product.product_uid, member_uids, since=window_start)
    price_series = pricing.daily_series(points, start=window_start.date(), end=now.date())
    if not price_series and product.price_series and not active:
        # A retired product keeps the chart it earned: its members have moved
        # on, but the history it accumulated is still true and still useful.
        price_series = list(product.price_series)

    # --- brand / part type -------------------------------------------------
    brand = _modal(o.brand for o in active)
    part_type = _modal(o.part_type for o in active)

    # --- publication gate --------------------------------------------------
    substance = len(part_numbers) + len(crossref_codes) + len(compatible) + len(incompatible)
    reasons = publication_gate(
        title=title,
        active_offers=len(active),
        has_price=any(o.price_toman is not None for o in active),
        substance_facts=substance,
        has_risky_family=any(row.risky_family for row in fitment_rows),
        risky_note=risky_note,
        has_explicit_verdict=bool(compatible or incompatible),
        is_retired=product.is_retired,
    )

    # --- persist the denormalised seller rows ------------------------------
    for offer in active:
        membership = membership_by_uid[offer.offer_uid]
        rank_row = ranked_by_uid[offer.offer_uid]
        seller = sellers.get(offer.seller_key)
        membership.seller_key = offer.seller_key
        membership.seller_name = seller.name if seller else offer.seller_key
        membership.price_toman = offer.price_toman
        membership.stock_status = offer.stock_status
        membership.authenticity_claim = offer.authenticity_claim
        membership.trust_score = trust_by_seller.get(offer.seller_key, 0.0)
        membership.rank_position = rank_row.rank_position
        membership.rank_score = rank_row.rank_score
        membership.url = offer.url
        membership.is_cheapest = offer.offer_uid == cheapest_uid
        membership.is_active = True
        membership.last_seen_at = offer.last_seen_at
    inactive_uids = set(member_uids) - {o.offer_uid for o in active}
    for uid in inactive_uids:
        row = membership_by_uid[uid]
        row.is_active = False
        row.is_cheapest = False
        row.rank_position = 0
    if memberships:
        ProductOffer.objects.bulk_update(
            memberships,
            [
                "seller_key", "seller_name", "price_toman", "stock_status",
                "authenticity_claim", "trust_score", "rank_position", "rank_score",
                "url", "is_cheapest", "is_active", "last_seen_at",
            ],
        )  # fmt: skip

    # --- the product row ---------------------------------------------------
    product.title = title
    product.brand = brand
    product.part_type = part_type
    product.authenticity_dominant = election.authenticity_dominant
    product.image_url = image_url
    product.representative_offer_uid = election.offer_uid
    product.representative_reason = election.reason
    product.part_numbers = part_numbers
    product.crossref_codes = crossref_codes
    product.vehicles_compatible = compatible
    product.vehicles_incompatible = incompatible
    product.vehicles_unknown = unknown
    product.risky_family_note_fa = risky_note
    product.offer_count = len(active)
    product.min_price_toman = stats.min_toman
    product.max_price_toman = stats.max_toman
    product.median_price_toman = stats.median_toman
    product.price_series = price_series
    product.is_published = not reasons
    product.unpublished_reasons = reasons
    product.slug = _assign_slug(product, title, now)
    product.updated_at = now

    document = build_document(product, active, ranked_by_uid, sellers, cheapest_uid)
    digest = document_hash(document)
    changed = digest != product.document_hash

    if not changed:
        # Nothing a consumer would notice moved. Keep the timestamp we
        # already published so the row — and the payload — stay byte-identical.
        product.updated_at = previous_updated_at
        document["updated_at"] = iso_utc(previous_updated_at)

    product.document = document
    product.document_hash = digest
    product.related = related.find_related(
        product.product_uid,
        part_type=part_type,
        vehicles_compatible=compatible,
        crossref_codes=crossref_codes,
        part_numbers=part_numbers,
    )
    product.seller_badges = {
        seller.seller_key: {
            "tier": seller.effective_tier,
            "is_new_seller": seller.is_new_seller,
            "trust_score": seller.trust_score,
        }
        for seller in sellers.values()
    }
    if changed and product.dirty_since is None:
        product.dirty_since = now
    product.save()

    logger.info(
        "product rebuilt",
        extra={
            "product_uid": str(product.product_uid),
            "offer_count": product.offer_count,
            "is_published": product.is_published,
            "unpublished_reasons": reasons,
            "changed": changed,
        },
    )
    return RebuildResult(product=product, changed=changed, document_hash=digest)


def _modal(values: Iterable[str | None]) -> str | None:
    """Most common non-null value; ties break alphabetically for stability."""
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        if value:
            counts[value] += 1
    if not counts:
        return None
    best = max(counts.values())
    return sorted(value for value, count in counts.items() if count == best)[0]


def build_document(
    product: Product,
    active: Sequence[OfferReadModel],
    ranked_by_uid: dict[str, ranking.RankedOffer],
    sellers: dict[str, Seller],
    cheapest_uid: str | None,
) -> dict[str, Any]:
    """The exact ``products.changed.v1`` payload.

    The read API serves this verbatim, so the event and the page can never
    disagree, and rendering a product costs one row rather than a join.
    """
    offers_out = []
    for offer in sorted(active, key=lambda o: ranked_by_uid[o.offer_uid].rank_position):
        seller = sellers.get(offer.seller_key)
        offers_out.append(
            {
                "offer_uid": offer.offer_uid,
                "seller_key": offer.seller_key,
                "seller_name": seller.name if seller else offer.seller_key,
                "price_toman": offer.price_toman,
                "stock_status": offer.stock_status,
                "authenticity_claim": offer.authenticity_claim,
                "trust_score": seller.trust_score if seller else 0.0,
                "rank_position": ranked_by_uid[offer.offer_uid].rank_position,
                "url": offer.url,
                "is_cheapest": offer.offer_uid == cheapest_uid,
            }
        )

    return {
        "product_uid": str(product.product_uid),
        "slug": product.slug,
        "title": product.title,
        "brand": product.brand,
        "part_type": product.part_type,
        "authenticity_dominant": product.authenticity_dominant,
        "image_url": product.image_url,
        "part_numbers": list(product.part_numbers),
        "crossref_codes": list(product.crossref_codes),
        "vehicles_compatible": list(product.vehicles_compatible),
        "vehicles_incompatible": list(product.vehicles_incompatible),
        "vehicles_unknown": list(product.vehicles_unknown),
        "risky_family_note_fa": product.risky_family_note_fa,
        "offer_count": product.offer_count,
        "min_price_toman": product.min_price_toman,
        "max_price_toman": product.max_price_toman,
        "median_price_toman": product.median_price_toman,
        "offers": offers_out,
        "price_series": list(product.price_series),
        "is_published": product.is_published,
        "successor_product_uid": (
            str(product.successor_product_uid) if product.successor_product_uid else None
        ),
        "updated_at": iso_utc(product.updated_at),
    }


def record_price_observation(
    offer_uid: str,
    product_uid: uuid.UUID | None,
    observed_at: dt.datetime,
    price_toman: int | None,
    stock_status: str,
    previous: tuple[int | None, str] | None,
) -> bool:
    """Append to price history — **on an actual change only**, never per event.

    Returns True when a row was written. The unique constraint on
    ``(offer_uid, observed_at)`` makes a replayed event a no-op rather than a
    duplicate point on the chart.
    """
    if previous is not None and previous == (price_toman, stock_status):
        return False
    _, created = PriceHistory.objects.get_or_create(
        offer_uid=offer_uid,
        observed_at=observed_at,
        defaults={
            "product_uid": product_uid,
            "price_toman": price_toman,
            "stock_status": stock_status or StockStatus.UNKNOWN,
        },
    )
    return created
