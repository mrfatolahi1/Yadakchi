"""Recompute seller trust and publish the sellers that moved.

Normally this runs on the Celery beat schedule; the command exists so an
operator can force it after changing the weights.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.tasks import recompute_trust


class Command(BaseCommand):
    help = "Recompute every seller's trust score and tier."

    def handle(self, *args: Any, **options: Any) -> None:
        changed = recompute_trust(timezone.now())
        self.stdout.write(f"sellers_changed={changed}")
