from __future__ import annotations

import logging
from uuid import uuid4

from django.core.management.base import BaseCommand
from django.db import transaction

from fitment.models import CrossRef, Vehicle
from fitment.producer import queue_crossref_changed, queue_vehicle_changed

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Emit the complete vehicle and cross-reference compacted reference state."

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        del args, options
        trace_id = uuid4().hex
        force_token = uuid4().hex
        vehicles = list(Vehicle.objects.all())
        crossrefs = list(CrossRef.objects.all())
        for vehicle in vehicles:
            queue_vehicle_changed(vehicle, trace_id=trace_id, force_token=force_token)
        for crossref in crossrefs:
            queue_crossref_changed(crossref, trace_id=trace_id, force_token=force_token)
        logger.info(
            "reference_state_queued",
            extra={
                "trace_id": trace_id,
                "vehicles": len(vehicles),
                "crossrefs": len(crossrefs),
            },
        )
