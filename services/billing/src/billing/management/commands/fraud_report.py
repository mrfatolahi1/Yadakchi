from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Count

from billing.models import ClickEvent
from billing.reporting import day_bounds

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Log per-seller suspicious click counts for a UTC day"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--day", required=True, type=date.fromisoformat)

    def handle(self, *args: Any, **options: Any) -> None:
        start, end = day_bounds(options["day"])
        rows = (
            ClickEvent.objects.filter(
                is_suspicious=True,
                occurred_at__gte=start,
                occurred_at__lt=end,
            )
            .values("seller_key")
            .annotate(count=Count("click_id"))
            .order_by("-count")
        )
        for row in rows:
            logger.info(
                "seller fraud report",
                extra={
                    "event": "seller_fraud_report",
                    "seller_key": row["seller_key"],
                    "count": row["count"],
                },
            )
