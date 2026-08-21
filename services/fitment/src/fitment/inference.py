from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from django.db import transaction
from django.utils import timezone

from fitment.models import (
    CrossRef,
    FitmentStatus,
    HumanCorrection,
    OfferReadModel,
    PartFitment,
    Provenance,
    ReviewRequestState,
    RiskyPartFamily,
    Vehicle,
)
from fitment.producer import queue_fitted, queue_review_request, stable_hash, utc_iso
from fitment.resolver import child_trims, resolve_vehicle_match
from fitment.text import normalize_persian

YEAR_RE = re.compile(r"(?<!\d)(?:13[7-9]\d|14\d{2}|19\d{2}|20\d{2})(?!\d)")
ENGINE_RE = re.compile(r"\b(?:tu3a?|tu5(?:jp4)?|xu7(?:jp4)?|ef7|m13|m15)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Claim:
    offer_uid: str
    seller_key: str
    title: str
    claim: str
    hint: str
    overbroad: bool

    def evidence(self) -> dict[str, object]:
        return {
            "offer_uid": self.offer_uid,
            "seller_key": self.seller_key,
            "title": self.title,
            "claim": self.claim,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class Verdict:
    status: str
    confidence: float
    provenance: str
    evidence: dict[str, Any]


def offer_group_key(payload: dict[str, Any]) -> str:
    part_number = payload.get("part_number")
    if part_number:
        return f"pn:{part_number}"
    brand = payload.get("brand")
    part_type = payload.get("part_type")
    if brand and part_type:
        return f"brand-type:{brand}:{part_type}"
    return f"offer:{payload['offer_uid']}"


def _seller_count(claims: list[Claim]) -> int:
    return len({claim.seller_key for claim in claims})


def _claim_map(
    offers: list[OfferReadModel],
) -> tuple[dict[str, list[Claim]], dict[str, list[Claim]]]:
    positive: dict[str, list[Claim]] = defaultdict(list)
    negative: dict[str, list[Claim]] = defaultdict(list)
    for offer in offers:
        for claim_name, hints, target in (
            ("compatible", offer.vehicle_hints, positive),
            ("incompatible", offer.vehicle_hints_excluded, negative),
        ):
            for hint in hints:
                match = resolve_vehicle_match(hint)
                if match is None:
                    continue
                target[match.vehicle_slug].append(
                    Claim(
                        offer_uid=offer.offer_uid,
                        seller_key=offer.seller_key,
                        title=offer.title_normalized,
                        claim=claim_name,
                        hint=hint,
                        overbroad=offer.overbroad_claim,
                    )
                )
    return positive, negative


def _computed_verdict(
    *,
    vehicle_slug: str,
    positives: list[Claim],
    negatives: list[Claim],
    part_number: str | None,
    trace_id: str,
) -> Verdict:
    positive_count = _seller_count(positives)
    negative_count = _seller_count(negatives)
    base_evidence: dict[str, Any] = {
        "part_number": part_number,
        "agreeing_sellers": positive_count,
        "disagreeing_sellers": negative_count,
        "positive_offers": [claim.evidence() for claim in positives],
        "negative_offers": [claim.evidence() for claim in negatives],
    }
    if positives and negatives:
        request_uid = ""
        review_should_emit = False
        if part_number:
            state: ReviewRequestState | None = None
            correction = HumanCorrection.objects.filter(
                part_number=part_number, vehicle_id=vehicle_slug
            ).first()
            if correction is not None:
                request_uid = correction.request_uid
                ReviewRequestState.objects.update_or_create(
                    part_number=part_number,
                    vehicle_id=vehicle_slug,
                    defaults={
                        "request_uid": correction.request_uid,
                        "state": ReviewRequestState.State.SETTLED,
                    },
                )
            else:
                state = ReviewRequestState.objects.filter(
                    part_number=part_number, vehicle_id=vehicle_slug
                ).first()
                if state is None:
                    request_uid = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"yadakchi:fitment-conflict:{part_number}:{vehicle_slug}:1",
                        )
                    )
                    ReviewRequestState.objects.create(
                        part_number=part_number,
                        vehicle_id=vehicle_slug,
                        request_uid=request_uid,
                    )
                    review_should_emit = True
                elif state.state == ReviewRequestState.State.SKIPPED:
                    state.attempt += 1
                    state.request_uid = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"yadakchi:fitment-conflict:{part_number}:{vehicle_slug}:{state.attempt}",
                        )
                    )
                    state.state = ReviewRequestState.State.PENDING
                    state.save(update_fields=["attempt", "request_uid", "state", "updated_at"])
                    request_uid = state.request_uid
                    review_should_emit = True
                else:
                    request_uid = state.request_uid
        base_evidence.update({"rule": "conflicting_seller_claims", "request_uid": request_uid})
        if part_number and review_should_emit:
            queue_review_request(
                request_uid=request_uid,
                subject={"part_number": part_number, "vehicle_slug": vehicle_slug},
                evidence={
                    "part_number": part_number,
                    "vehicle_slug": vehicle_slug,
                    "compatible_claims": [claim.evidence() for claim in positives],
                    "incompatible_claims": [claim.evidence() for claim in negatives],
                },
                trace_id=trace_id,
            )
        return Verdict(FitmentStatus.UNKNOWN, 0.0, Provenance.RULE, base_evidence)
    if positives and all(claim.overbroad for claim in positives):
        base_evidence["rule"] = "overbroad_claim_only"
        return Verdict(FitmentStatus.UNKNOWN, 0.1, Provenance.RULE, base_evidence)
    if positive_count >= 5:
        base_evidence["rule"] = "consensus_part_number"
        return Verdict(FitmentStatus.COMPATIBLE, 0.94, Provenance.CONSENSUS, base_evidence)
    if positive_count >= 2:
        base_evidence["rule"] = "consensus_part_number"
        return Verdict(FitmentStatus.COMPATIBLE, 0.68, Provenance.CONSENSUS, base_evidence)
    if positive_count == 1:
        base_evidence["rule"] = "single_seller_claim"
        return Verdict(FitmentStatus.UNKNOWN, 0.21, Provenance.RULE, base_evidence)
    base_evidence["rule"] = "negative_claim_only"
    base_evidence["note"] = "Seller exclusions never automatically produce incompatible."
    return Verdict(FitmentStatus.UNKNOWN, 0.1, Provenance.RULE, base_evidence)


def _granularity_present(required: str, text: str) -> bool:
    has_year = bool(YEAR_RE.search(text))
    has_engine = bool(ENGINE_RE.search(text))
    if required == "year":
        return has_year
    if required == "engine":
        return has_engine
    if required == "engine_or_year":
        return has_engine or has_year
    if required == "year_and_engine":
        return has_engine and has_year
    return False


def _human_verdict(correction: HumanCorrection) -> Verdict:
    return Verdict(
        status=correction.status,
        confidence=1.0,
        provenance=Provenance.HUMAN,
        evidence={
            "rule": "human_correction",
            "request_uid": correction.request_uid,
            "actor": correction.actor,
            "reason": correction.reason,
            "settled_unknown": correction.status == FitmentStatus.UNKNOWN,
        },
    )


def _semantic_payload(
    *,
    offer: OfferReadModel,
    verdicts: dict[str, Verdict],
    risky: RiskyPartFamily | None,
) -> dict[str, Any]:
    corrections = (
        {
            correction.vehicle_id: correction
            for correction in HumanCorrection.objects.filter(part_number=offer.part_number)
        }
        if offer.part_number
        else {}
    )
    entries: list[dict[str, Any]] = []
    all_slugs = sorted(set(verdicts) | set(corrections))
    normalized_text = normalize_persian(
        " ".join([offer.title_normalized, *offer.vehicle_hints, *offer.vehicle_hints_excluded])
    )
    missing_risky_detail = bool(
        risky and not _granularity_present(risky.required_granularity, normalized_text)
    )
    for slug in all_slugs:
        verdict = _human_verdict(corrections[slug]) if slug in corrections else verdicts[slug]
        if missing_risky_detail and risky is not None and slug not in corrections:
            evidence = {
                **verdict.evidence,
                "rule": "risky_family_missing_granularity",
                "required_granularity": risky.required_granularity,
            }
            verdict = Verdict(FitmentStatus.UNKNOWN, 0.18, Provenance.RULE, evidence)
        entries.append(
            {
                "vehicle_slug": slug,
                "status": str(verdict.status),
                "confidence": verdict.confidence,
                "provenance": str(verdict.provenance),
                "evidence": verdict.evidence,
            }
        )
    crossref_codes: set[str] = set()
    if offer.part_number:
        for crossref in CrossRef.objects.filter(code_a=offer.part_number) | CrossRef.objects.filter(
            code_b=offer.part_number
        ):
            crossref_codes.add(
                crossref.code_b if crossref.code_a == offer.part_number else crossref.code_a
            )
    risky_payload = (
        {
            "part_type": risky.part_type,
            "required_granularity": risky.required_granularity,
            "note_fa": risky.note_fa,
        }
        if missing_risky_detail and risky
        else None
    )
    return {
        "offer_uid": offer.offer_uid,
        "fitments": entries,
        "crossref_codes": sorted(crossref_codes),
        "risky_family": risky_payload,
    }


def _persist_offer_output(
    offer: OfferReadModel,
    semantic_payload: dict[str, Any],
    *,
    trace_id: str,
    computed_at: datetime,
) -> bool:
    semantic = stable_hash(semantic_payload)
    if offer.output_hash == semantic:
        return False
    vehicles = Vehicle.objects.in_bulk(
        [entry["vehicle_slug"] for entry in semantic_payload["fitments"]]
    )
    PartFitment.objects.filter(offer=offer).delete()
    PartFitment.objects.bulk_create(
        [
            PartFitment(
                offer=offer,
                vehicle=vehicles[entry["vehicle_slug"]],
                status=entry["status"],
                confidence=entry["confidence"],
                provenance=entry["provenance"],
                evidence=entry["evidence"],
                computed_at=computed_at,
            )
            for entry in semantic_payload["fitments"]
        ]
    )
    payload = {**semantic_payload, "computed_at": utc_iso(computed_at)}
    queue_fitted(
        offer_uid=offer.offer_uid, payload=payload, semantic_hash=semantic, trace_id=trace_id
    )
    offer.output_hash = semantic
    offer.save(update_fields=["output_hash", "updated_at"])
    return True


@transaction.atomic
def recompute_group(group_key: str, *, trace_id: str) -> int:
    all_offers = list(OfferReadModel.objects.select_for_update().filter(group_key=group_key))
    active_offers = [offer for offer in all_offers if offer.is_active]
    positive, negative = _claim_map(active_offers)
    target_slugs = set(positive) | set(negative)
    part_number = next((offer.part_number for offer in active_offers if offer.part_number), None)
    verdicts = {
        slug: _computed_verdict(
            vehicle_slug=slug,
            positives=positive.get(slug, []),
            negatives=negative.get(slug, []),
            part_number=part_number,
            trace_id=trace_id,
        )
        for slug in target_slugs
    }

    for slug in list(target_slugs):
        vehicle = Vehicle.objects.filter(slug=slug, trim__isnull=True).first()
        if vehicle is None or not positive.get(slug):
            continue
        for child in child_trims(vehicle):
            verdicts.setdefault(
                child.slug,
                Verdict(
                    FitmentStatus.UNKNOWN,
                    0.0,
                    Provenance.RULE,
                    {
                        "rule": "model_level_only",
                        "resolved_to": vehicle.slug,
                        "excluded_from_coverage_denominator": True,
                    },
                ),
            )

    risky_by_type = RiskyPartFamily.objects.in_bulk(
        {offer.part_type for offer in all_offers if offer.part_type}, field_name="part_type"
    )
    computed_at = timezone.now()
    changed = 0
    for offer in all_offers:
        semantic_payload = _semantic_payload(
            offer=offer,
            verdicts=verdicts,
            risky=risky_by_type.get(offer.part_type) if offer.part_type else None,
        )
        changed += _persist_offer_output(
            offer, semantic_payload, trace_id=trace_id, computed_at=computed_at
        )
    return changed


def recompute_all(*, trace_id: str) -> int:
    changed = 0
    for group_key in OfferReadModel.objects.values_list("group_key", flat=True).distinct():
        changed += recompute_group(group_key, trace_id=trace_id)
    return changed
