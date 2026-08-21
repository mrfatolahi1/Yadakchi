from __future__ import annotations

from datetime import UTC, datetime

from django.test import override_settings

from fitment.coverage import COVERAGE_OFFERS, COVERAGE_RATIO, compute_coverage, request_publication
from fitment.crossref import store_crossref
from fitment.models import OfferReadModel, OutboxEvent, PartFitment, Vehicle


def create_offer(number: int) -> OfferReadModel:
    value = f"{number:032x}"
    return OfferReadModel.objects.create(
        offer_uid=value,
        source_key="source",
        external_key=value,
        seller_key=f"seller-{number}",
        url=f"https://example.com/{value}",
        raw_title="لنت ترمز",
        title_normalized="لنت ترمز",
        brand="brand",
        part_number=f"PN{number}",
        part_type="brake_pad_front",
        price_toman=1000,
        vehicle_hints=[],
        vehicle_hints_excluded=[],
        overbroad_claim=False,
        is_active=True,
        group_key=f"pn:PN{number}",
        trace_id="trace",
        source_occurred_at=datetime.now(UTC),
        payload_hash=str(number),
    )


@override_settings(FITMENT_COVERAGE_MIN_OFFERS=3, FITMENT_COVERAGE_THRESHOLD=0.70)
def test_coverage_below_seventy_refuses_publication_and_emits_metrics(seeded: None) -> None:
    vehicle = Vehicle.objects.get(slug="peugeot-206-type-5")
    vehicle.is_published = True
    vehicle.save()
    for number, status in enumerate(["compatible", "compatible", "unknown"], start=1):
        PartFitment.objects.create(
            offer=create_offer(number),
            vehicle=vehicle,
            status=status,
            confidence=0.5,
            provenance="rule",
            evidence={"rule": "coverage_test"},
            computed_at=datetime.now(UTC),
        )
    result = request_publication(vehicle)
    vehicle.refresh_from_db()
    assert result.denominator == 3
    assert result.ratio == 2 / 3
    assert result.publishable is False
    assert vehicle.is_published is False
    assert COVERAGE_RATIO.labels(vehicle=vehicle.slug)._value.get() == 2 / 3
    assert COVERAGE_OFFERS.labels(vehicle=vehicle.slug)._value.get() == 3


def test_model_level_only_is_excluded_from_coverage(seeded: None) -> None:
    vehicle = Vehicle.objects.get(slug="peugeot-206-type-5")
    PartFitment.objects.create(
        offer=create_offer(10),
        vehicle=vehicle,
        status="unknown",
        confidence=0,
        provenance="rule",
        evidence={"rule": "model_level_only"},
        computed_at=datetime.now(UTC),
    )
    assert compute_coverage(vehicle).denominator == 0


def test_crossref_order_is_normalized_and_stored_once(db: object) -> None:
    del db
    first, created = store_crossref(
        "ZZ-200", "AA-100", brand_a="z", brand_b="a", confidence=0.9, provenance="human"
    )
    second, created_again = store_crossref(
        "AA100", "ZZ200", brand_a="a", brand_b="z", confidence=0.9, provenance="human"
    )
    assert created is True
    assert created_again is False
    assert first.pk == second.pk
    assert (first.code_a, first.code_b) == ("AA100", "ZZ200")
    assert OutboxEvent.objects.filter(topic="yadakchi.crossrefs.changed.v1").count() == 1
