from __future__ import annotations

import logging
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandParser

from fitment.coverage import compute_all_coverage, request_publication
from fitment.inference import recompute_all
from fitment.models import Vehicle

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recompute all fitments and coverage while preserving human corrections."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply-publication-gate", action="store_true")

    def handle(self, *args: object, **options: object) -> None:
        del args
        trace_id = uuid4().hex
        changed = recompute_all(trace_id=trace_id)
        results = (
            [request_publication(vehicle) for vehicle in Vehicle.objects.all()]
            if options["apply_publication_gate"]
            else compute_all_coverage()
        )
        logger.info(
            "full_recompute_completed",
            extra={"trace_id": trace_id, "changed_offers": changed, "vehicles": len(results)},
        )
