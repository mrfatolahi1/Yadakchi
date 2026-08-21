from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from fitment.consumers import process_decision_event, process_offer_event
from fitment.inference import recompute_all
from fitment.models import HumanCorrection, OutboxEvent, PartFitment


def uid(number: int) -> str:
    return f"{number:032x}"


def test_bare_model_never_guesses_trim(
    seeded: None, offer_event: Callable[..., dict[str, Any]]
) -> None:
    process_offer_event(
        offer_event(offer_uid=uid(1), vehicle_hints=["206"], title="لنت ترمز پژو 206")
    )
    model = PartFitment.objects.get(offer_id=uid(1), vehicle_id="peugeot-206")
    assert model.status == "unknown"
    trims = PartFitment.objects.filter(
        offer_id=uid(1), vehicle__model="206", vehicle__trim__isnull=False
    )
    assert trims.count() == 4
    assert all(row.status == "unknown" for row in trims)
    assert all(row.evidence["rule"] == "model_level_only" for row in trims)


def test_overbroad_only_is_unknown(
    seeded: None, offer_event: Callable[..., dict[str, Any]]
) -> None:
    process_offer_event(
        offer_event(
            offer_uid=uid(2),
            vehicle_hints=["پژو 206 تیپ 5"],
            overbroad_claim=True,
            title="مناسب تمام پژوها",
        )
    )
    row = PartFitment.objects.get(offer_id=uid(2), vehicle_id="peugeot-206-type-5")
    assert row.status == "unknown"
    assert row.evidence["rule"] == "overbroad_claim_only"


def test_consensus_thresholds(seeded: None, offer_event: Callable[..., dict[str, Any]]) -> None:
    process_offer_event(offer_event(offer_uid=uid(10), seller_key="seller-1"))
    assert PartFitment.objects.get(offer_id=uid(10)).status == "unknown"
    for number in range(11, 15):
        process_offer_event(offer_event(offer_uid=uid(number), seller_key=f"seller-{number - 9}"))
    rows = PartFitment.objects.filter(vehicle_id="peugeot-206-type-5")
    assert rows.count() == 5
    assert all(row.status == "compatible" for row in rows)
    assert all(row.provenance == "consensus" for row in rows)
    assert all(row.confidence == pytest.approx(0.94) for row in rows)


def test_negative_consensus_never_auto_produces_incompatible(
    seeded: None, offer_event: Callable[..., dict[str, Any]]
) -> None:
    for number in range(20, 25):
        process_offer_event(
            offer_event(
                offer_uid=uid(number),
                seller_key=f"negative-{number}",
                vehicle_hints=[],
                vehicle_hints_excluded=["پژو 206 تیپ 5"],
            )
        )
    rows = PartFitment.objects.filter(vehicle_id="peugeot-206-type-5")
    assert rows.count() == 5
    assert all(row.status == "unknown" for row in rows)
    assert all(row.evidence["rule"] == "negative_claim_only" for row in rows)


def test_conflict_is_unknown_and_requests_self_contained_review(
    seeded: None, offer_event: Callable[..., dict[str, Any]]
) -> None:
    process_offer_event(offer_event(offer_uid=uid(30), seller_key="positive"))
    process_offer_event(
        offer_event(
            offer_uid=uid(31),
            seller_key="negative",
            vehicle_hints=[],
            vehicle_hints_excluded=["پژو 206 تیپ 5"],
        )
    )
    assert PartFitment.objects.get(offer_id=uid(30)).status == "unknown"
    review = OutboxEvent.objects.get(topic="yadakchi.review.requested.v1")
    evidence = review.envelope["payload"]["evidence"]
    assert evidence["part_number"] == "425438"
    assert evidence["vehicle_slug"] == "peugeot-206-type-5"
    assert evidence["compatible_claims"][0]["seller_key"] == "positive"
    assert evidence["incompatible_claims"][0]["seller_key"] == "negative"


def test_skipped_conflict_can_be_requeued_with_new_identity(
    seeded: None,
    offer_event: Callable[..., dict[str, Any]],
    decision_event: Callable[..., dict[str, Any]],
) -> None:
    process_offer_event(offer_event(offer_uid=uid(32), seller_key="positive"))
    process_offer_event(
        offer_event(
            offer_uid=uid(33),
            seller_key="negative",
            vehicle_hints=[],
            vehicle_hints_excluded=["پژو 206 تیپ 5"],
        )
    )
    first = OutboxEvent.objects.get(topic="yadakchi.review.requested.v1")
    skipped = decision_event(
        part_number="425438",
        vehicle_slug="peugeot-206-type-5",
        status="unknown",
        request_uid=first.message_key,
    )
    skipped["payload"]["decision"] = "skip"
    skipped["payload"]["subject"].pop("status")
    process_decision_event(skipped)
    recompute_all(trace_id="retry-skipped-review")
    requests = list(
        OutboxEvent.objects.filter(topic="yadakchi.review.requested.v1").order_by("created_at")
    )
    assert len(requests) == 2
    assert requests[0].message_key != requests[1].message_key


def test_risky_headlight_without_year_is_unknown_with_warning(
    seeded: None, offer_event: Callable[..., dict[str, Any]]
) -> None:
    for number in range(40, 45):
        process_offer_event(
            offer_event(
                offer_uid=uid(number),
                seller_key=f"seller-{number}",
                part_type="headlight_right",
                title="چراغ جلو راست پژو 206 تیپ 5",
            )
        )
    row = PartFitment.objects.get(offer_id=uid(44), vehicle_id="peugeot-206-type-5")
    assert row.status == "unknown"
    assert row.evidence["rule"] == "risky_family_missing_granularity"
    event = OutboxEvent.objects.filter(
        topic="yadakchi.offers.fitted.v1", message_key=uid(44)
    ).latest("created_at")
    risky = event.envelope["payload"]["risky_family"]
    assert risky["part_type"] == "headlight_right"
    assert risky["required_granularity"] == "year"


def test_human_incompatible_survives_full_recompute(
    seeded: None,
    offer_event: Callable[..., dict[str, Any]],
    decision_event: Callable[..., dict[str, Any]],
) -> None:
    process_offer_event(offer_event(offer_uid=uid(50), seller_key="seller-a"))
    process_offer_event(offer_event(offer_uid=uid(51), seller_key="seller-b"))
    process_decision_event(
        decision_event(
            part_number="425438", vehicle_slug="peugeot-206-type-5", status="incompatible"
        )
    )
    assert HumanCorrection.objects.count() == 1
    recompute_all(trace_id="full-recompute")
    rows = PartFitment.objects.filter(vehicle_id="peugeot-206-type-5")
    assert all(row.status == "incompatible" for row in rows)
    assert all(row.provenance == "human" for row in rows)


def test_preexisting_human_correction_prevents_conflict_requeue(
    seeded: None,
    offer_event: Callable[..., dict[str, Any]],
    decision_event: Callable[..., dict[str, Any]],
) -> None:
    process_decision_event(
        decision_event(
            part_number="425438",
            vehicle_slug="peugeot-206-type-5",
            status="incompatible",
            request_uid="decision-before-offers",
        )
    )
    process_offer_event(offer_event(offer_uid=uid(52), seller_key="positive"))
    process_offer_event(
        offer_event(
            offer_uid=uid(53),
            seller_key="negative",
            vehicle_hints=[],
            vehicle_hints_excluded=["پژو 206 تیپ 5"],
        )
    )
    rows = PartFitment.objects.filter(vehicle_id="peugeot-206-type-5")
    assert all(row.status == "incompatible" for row in rows)
    assert OutboxEvent.objects.filter(topic="yadakchi.review.requested.v1").count() == 0


def test_duplicate_offer_delivery_writes_once_and_emits_once(
    seeded: None, offer_event: Callable[..., dict[str, Any]]
) -> None:
    event = offer_event(offer_uid=uid(60), event_id="d8083bb6-9254-4f33-8f2c-843304121b32")
    assert process_offer_event(event) is True
    original_rows = list(
        PartFitment.objects.filter(offer_id=uid(60)).values(
            "vehicle_id", "status", "confidence", "provenance", "evidence"
        )
    )
    assert process_offer_event(event) is False
    duplicate_rows = list(
        PartFitment.objects.filter(offer_id=uid(60)).values(
            "vehicle_id", "status", "confidence", "provenance", "evidence"
        )
    )
    assert duplicate_rows == original_rows
    assert (
        OutboxEvent.objects.filter(topic="yadakchi.offers.fitted.v1", message_key=uid(60)).count()
        == 1
    )
