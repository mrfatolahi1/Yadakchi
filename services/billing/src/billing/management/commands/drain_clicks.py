from __future__ import annotations

import logging
import time
from typing import Any

from django.core.management.base import BaseCommand

from billing.drain import drain_clicks

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Drain Redis click intents into Postgres and publish the durable outbox"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args: Any, **options: Any) -> None:
        first_run = True
        while True:
            processed = drain_clicks(
                limit=options["batch_size"],
                recover=first_run,
            )
            first_run = False
            if processed:
                logger.info(
                    "click batch drained",
                    extra={"event": "click_batch_drained", "count": processed},
                )
            if options["once"]:
                return
            if not processed:
                time.sleep(0.25)
