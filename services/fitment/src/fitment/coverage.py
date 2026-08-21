from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from prometheus_client import Gauge

from fitment.models import FitmentStatus, PartFitment, Vehicle

COVERAGE_RATIO = Gauge(
    "yadakchi_fitment_coverage_ratio", "Non-unknown fitment coverage", ["vehicle"]
)
COVERAGE_OFFERS = Gauge(
    "yadakchi_fitment_coverage_offers", "Offers evaluated for fitment coverage", ["vehicle"]
)
COVERED_PART_TYPES = Gauge(
    "yadakchi_fitment_covered_part_types",
    "Distinct part types with a compatible verdict",
    ["vehicle"],
)


@dataclass(frozen=True)
class CoverageResult:
    vehicle_slug: str
    numerator: int
    denominator: int
    ratio: float
    covered_part_types: int
    publishable: bool


def compute_coverage(vehicle: Vehicle) -> CoverageResult:
    rows = list(
        PartFitment.objects.filter(vehicle=vehicle, offer__is_active=True).select_related("offer")
    )
    in_denominator = [row for row in rows if row.evidence.get("rule") != "model_level_only"]
    numerator = sum(row.status != FitmentStatus.UNKNOWN for row in in_denominator)
    denominator = len(in_denominator)
    ratio = numerator / denominator if denominator else 0.0
    part_types = {
        row.offer.part_type
        for row in in_denominator
        if row.status == FitmentStatus.COMPATIBLE and row.offer.part_type
    }
    publishable = (
        denominator >= settings.FITMENT_COVERAGE_MIN_OFFERS
        and ratio >= settings.FITMENT_COVERAGE_THRESHOLD
    )
    COVERAGE_RATIO.labels(vehicle=vehicle.slug).set(ratio)
    COVERAGE_OFFERS.labels(vehicle=vehicle.slug).set(denominator)
    COVERED_PART_TYPES.labels(vehicle=vehicle.slug).set(len(part_types))
    return CoverageResult(
        vehicle_slug=vehicle.slug,
        numerator=numerator,
        denominator=denominator,
        ratio=ratio,
        covered_part_types=len(part_types),
        publishable=publishable,
    )


def request_publication(vehicle: Vehicle) -> CoverageResult:
    result = compute_coverage(vehicle)
    desired = result.publishable
    if vehicle.is_published != desired:
        vehicle.is_published = desired
        vehicle.save(update_fields=["is_published", "updated_at"])
    return result


def compute_all_coverage() -> list[CoverageResult]:
    return [compute_coverage(vehicle) for vehicle in Vehicle.objects.all()]
