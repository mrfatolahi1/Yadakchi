from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from django.core.management.base import BaseCommand
from django.db import transaction

from fitment.models import RiskyPartFamily, Vehicle

logger = logging.getLogger(__name__)
SEED_DIR = Path(__file__).resolve().parents[2] / "seed"


def load_yaml(name: str) -> dict[str, Any]:
    with (SEED_DIR / name).open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    if not isinstance(loaded, dict):
        raise ValueError(f"Seed {name} must contain a mapping.")
    return loaded


def upsert_vehicle(row: dict[str, Any]) -> bool:
    slug = str(row["slug"])
    values = {key: value for key, value in row.items() if key != "slug"}
    vehicle = Vehicle.objects.filter(slug=slug).first()
    if vehicle is None:
        Vehicle.objects.create(slug=slug, is_published=False, **values)
        return True
    changed = False
    for field, value in values.items():
        if getattr(vehicle, field) != value:
            setattr(vehicle, field, value)
            changed = True
    if changed:
        vehicle.full_clean()
        vehicle.save()
    return changed


def upsert_risky_family(row: dict[str, Any]) -> bool:
    part_type = str(row["part_type"])
    values = {key: value for key, value in row.items() if key != "part_type"}
    family = RiskyPartFamily.objects.filter(part_type=part_type).first()
    if family is None:
        RiskyPartFamily.objects.create(part_type=part_type, **values)
        return True
    changed = False
    for field, value in values.items():
        if getattr(family, field) != value:
            setattr(family, field, value)
            changed = True
    if changed:
        family.save()
    return changed


class Command(BaseCommand):
    help = "Idempotently load the hand-written vehicle tree and risky part families."

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        del args, options
        vehicles = load_yaml("vehicles.yaml").get("vehicles", [])
        risky_families = load_yaml("risky_families.yaml").get("risky_families", [])
        changed_vehicles = sum(upsert_vehicle(row) for row in vehicles)
        changed_risky = sum(upsert_risky_family(row) for row in risky_families)
        logger.info(
            "reference_seed_completed",
            extra={
                "trace_id": "seed-vehicles",
                "vehicle_rows": len(vehicles),
                "changed_vehicles": changed_vehicles,
                "risky_rows": len(risky_families),
                "changed_risky": changed_risky,
            },
        )
