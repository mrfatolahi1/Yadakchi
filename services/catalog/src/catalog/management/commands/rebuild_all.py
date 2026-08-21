"""Rebuild every product from the local read models.

This is the algorithm-change path: when representative selection, ranking or
the publication gate changes, nothing needs re-crawling and nothing needs
re-clustering — the read models already hold every input, so the whole
catalogue is recomputed in place. Reprocessing is routine here, not
exceptional.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from catalog import producer
from catalog.models import Product
from catalog.rebuild import rebuild_product
from catalog.tasks import emit_product


class Command(BaseCommand):
    help = "Rebuild every product; optionally emit products.changed immediately."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--emit",
            action="store_true",
            help="Publish changed products now instead of waiting for the debounce flush.",
        )
        parser.add_argument("--limit", type=int, default=None, help="Rebuild at most N products.")
        parser.add_argument(
            "--product-uid", default=None, help="Rebuild a single product and stop."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        queryset = Product.objects.order_by("created_at").values_list("product_uid", flat=True)
        if options["product_uid"]:
            queryset = queryset.filter(product_uid=options["product_uid"])
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        rebuilt = changed = emitted = 0
        for product_uid in list(queryset):
            result = rebuild_product(product_uid, now)
            if result is None:
                continue
            rebuilt += 1
            if result.changed:
                changed += 1
                if options["emit"] and emit_product(result.product, now):
                    emitted += 1

        if options["emit"]:
            producer.flush()

        self.stdout.write(
            f"rebuilt={rebuilt} changed={changed} emitted={emitted}",
        )
