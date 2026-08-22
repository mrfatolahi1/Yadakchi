from __future__ import annotations

import csv
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Max
from django.utils import timezone

from search.models import QueryLog


class Command(BaseCommand):
    help = "Write the weekly zero-result query work queue as CSV."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        since = timezone.now() - timedelta(days=7)
        rows = (
            QueryLog.objects.filter(result_count=0, created_at__gte=since)
            .values("normalized_text", "vehicle_slug", "filters")
            .annotate(query_count=Count("query_id"), last_seen=Max("created_at"))
            .order_by("-query_count", "normalized_text")
        )
        writer = csv.writer(self.stdout)
        writer.writerow(
            ("normalized_text", "vehicle_slug", "filters", "query_count", "last_seen_utc")
        )
        for row in rows:
            writer.writerow(
                (
                    row["normalized_text"],
                    row["vehicle_slug"] or "",
                    row["filters"],
                    row["query_count"],
                    row["last_seen"].isoformat(),
                )
            )
