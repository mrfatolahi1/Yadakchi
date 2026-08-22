from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from billing.reporting import reconcile_day

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconcile click costs against wallet charge transactions"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--day", type=date.fromisoformat)

    def handle(self, *args: Any, **options: Any) -> None:
        day = options["day"] or (timezone.localdate() - timedelta(days=1))
        discrepancies = reconcile_day(day)
        if discrepancies:
            logger.error(
                "billing reconciliation failed",
                extra={"event": "billing_reconciliation_failed", "day": str(day)},
            )
            raise CommandError("; ".join(discrepancies))
        logger.info(
            "billing reconciliation passed",
            extra={"event": "billing_reconciliation_passed", "day": str(day)},
        )
