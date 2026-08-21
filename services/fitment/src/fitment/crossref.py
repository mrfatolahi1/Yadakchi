from __future__ import annotations

import re
from collections.abc import Iterable

from django.db import transaction

from fitment.models import CrossRef, OfferReadModel, Provenance
from fitment.text import normalize_part_number, normalize_persian

EQUIVALENCE_RE = re.compile(r"(?:معادل|جایگزین|equivalent|replacement)", re.IGNORECASE)
CODE_RE = re.compile(r"(?<![A-Z0-9])[A-Z0-9][A-Z0-9-]{3,}(?![A-Z0-9])", re.IGNORECASE)


def ordered_pair(
    code_a: str, code_b: str, brand_a: str | None = None, brand_b: str | None = None
) -> tuple[str, str, str | None, str | None]:
    first = normalize_part_number(code_a)
    second = normalize_part_number(code_b)
    if first is None or second is None or first == second:
        raise ValueError("Cross-reference codes must be distinct non-empty part numbers.")
    if second < first:
        return second, first, brand_b, brand_a
    return first, second, brand_a, brand_b


@transaction.atomic
def store_crossref(
    code_a: str,
    code_b: str,
    *,
    brand_a: str | None = None,
    brand_b: str | None = None,
    confidence: float,
    provenance: str,
) -> tuple[CrossRef, bool]:
    first, second, first_brand, second_brand = ordered_pair(code_a, code_b, brand_a, brand_b)
    existing = CrossRef.objects.filter(code_a=first, code_b=second).first()
    values = {
        "brand_a": first_brand,
        "brand_b": second_brand,
        "confidence": confidence,
        "provenance": provenance,
    }
    if existing is None:
        return CrossRef.objects.create(code_a=first, code_b=second, **values), True
    changed = any(getattr(existing, field) != value for field, value in values.items())
    if changed:
        for field, value in values.items():
            setattr(existing, field, value)
        existing.save()
    return existing, changed


def infer_title_crossrefs(offer: OfferReadModel) -> list[CrossRef]:
    normalized_title = normalize_persian(offer.title_normalized)
    if not EQUIVALENCE_RE.search(normalized_title) or not offer.part_number:
        return []
    codes = {
        normalize_part_number(code) for code in CODE_RE.findall(offer.title_normalized.upper())
    }
    codes.discard(None)
    codes.discard(offer.part_number)
    created: list[CrossRef] = []
    for code in sorted(cast_codes(codes)):
        crossref, _ = store_crossref(
            offer.part_number,
            code,
            brand_a=offer.brand,
            confidence=0.75,
            provenance=Provenance.RULE,
        )
        created.append(crossref)
    return created


def cast_codes(values: Iterable[str | None]) -> set[str]:
    return {value for value in values if value is not None}


def infer_shared_fitment_crossrefs(part_type: str | None) -> list[CrossRef]:
    if not part_type:
        return []
    groups: dict[str, dict[str, object]] = {}
    for offer in OfferReadModel.objects.filter(
        part_type=part_type,
        part_number__isnull=False,
        brand__isnull=False,
        price_toman__isnull=False,
        is_active=True,
    ).prefetch_related("fitments"):
        assert offer.part_number is not None
        entry = groups.setdefault(
            offer.part_number,
            {"brands": set(), "prices": [], "sellers": set(), "vehicles": set()},
        )
        if offer.brand:
            cast_set(entry["brands"]).add(offer.brand)
        cast_list(entry["prices"]).append(offer.price_toman)
        cast_set(entry["sellers"]).add(offer.seller_key)
        cast_set(entry["vehicles"]).update(
            fitment.vehicle_id for fitment in offer.fitments.all() if fitment.status == "compatible"
        )

    created: list[CrossRef] = []
    codes = sorted(groups)
    for index, code_a in enumerate(codes):
        data_a = groups[code_a]
        if len(cast_set(data_a["sellers"])) < 3 or not cast_set(data_a["vehicles"]):
            continue
        for code_b in codes[index + 1 :]:
            data_b = groups[code_b]
            if len(cast_set(data_b["sellers"])) < 3:
                continue
            if cast_set(data_a["brands"]) & cast_set(data_b["brands"]):
                continue
            if cast_set(data_a["vehicles"]) != cast_set(data_b["vehicles"]):
                continue
            median_a = median(cast_list(data_a["prices"]))
            median_b = median(cast_list(data_b["prices"]))
            if (
                min(median_a, median_b) == 0
                or max(median_a, median_b) / min(median_a, median_b) > 1.2
            ):
                continue
            brand_a = sorted(cast_set(data_a["brands"]))[0]
            brand_b = sorted(cast_set(data_b["brands"]))[0]
            crossref, _ = store_crossref(
                code_a,
                code_b,
                brand_a=brand_a,
                brand_b=brand_b,
                confidence=0.7,
                provenance=Provenance.CONSENSUS,
            )
            created.append(crossref)
    return created


def median(values: list[object]) -> float:
    numbers = sorted(float(value) for value in values if isinstance(value, (int, float)))
    midpoint = len(numbers) // 2
    if len(numbers) % 2:
        return numbers[midpoint]
    return (numbers[midpoint - 1] + numbers[midpoint]) / 2


def cast_set(value: object) -> set[str]:
    assert isinstance(value, set)
    return value


def cast_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value
