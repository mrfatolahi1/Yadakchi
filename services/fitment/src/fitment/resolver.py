from __future__ import annotations

from dataclasses import dataclass

from fitment.models import Vehicle
from fitment.text import contains_normalized, normalize_persian


@dataclass(frozen=True)
class VehicleMatch:
    vehicle_slug: str
    matched_alias: str
    normalized_alias: str
    is_model_level: bool


def _candidate_aliases(vehicle: Vehicle) -> set[str]:
    return {vehicle.display_name_fa, vehicle.slug.replace("-", " "), *vehicle.aliases}


def resolve_vehicle_match(text: str) -> VehicleMatch | None:
    normalized_text = normalize_persian(text)
    matches: list[tuple[int, int, int, Vehicle, str]] = []
    for vehicle in Vehicle.objects.all():
        for alias in _candidate_aliases(vehicle):
            normalized_alias = normalize_persian(alias)
            if contains_normalized(normalized_text, normalized_alias):
                specificity = 1 if vehicle.trim else 0
                matches.append(
                    (
                        len(normalized_alias.split()),
                        len(normalized_alias),
                        specificity,
                        vehicle,
                        alias,
                    )
                )
    if not matches:
        return None
    _, _, _, vehicle, alias = max(matches, key=lambda item: (item[0], item[1], item[2]))
    return VehicleMatch(
        vehicle_slug=vehicle.slug,
        matched_alias=alias,
        normalized_alias=normalize_persian(alias),
        is_model_level=vehicle.trim is None,
    )


def resolve_vehicle(text: str) -> str | None:
    match = resolve_vehicle_match(text)
    return match.vehicle_slug if match else None


def child_trims(model_vehicle: Vehicle) -> list[Vehicle]:
    if model_vehicle.trim is not None:
        return []
    return list(
        Vehicle.objects.filter(
            brand=model_vehicle.brand,
            model=model_vehicle.model,
            trim__isnull=False,
        ).order_by("slug")
    )
