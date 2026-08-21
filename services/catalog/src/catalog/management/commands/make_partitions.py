"""Create the PriceHistory partitions for the coming months."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from catalog.partitions import ensure_partitions


class Command(BaseCommand):
    help = "Create monthly PriceHistory partitions. Idempotent."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--months-ahead", type=int, default=settings.PARTITION_MONTHS_AHEAD)

    def handle(self, *args: Any, **options: Any) -> None:
        created = ensure_partitions(options["months_ahead"])
        self.stdout.write("\n".join(created))
