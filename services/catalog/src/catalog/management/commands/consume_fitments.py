"""Consume fitment verdicts from fitment."""

from __future__ import annotations

from django.conf import settings

from catalog.consumers import handlers
from catalog.management.commands._consumer_base import ConsumerCommand


class Command(ConsumerCommand):
    help = "Consume fitment verdicts from fitment."
    topic = settings.TOPIC_OFFERS_FITTED
    group_purpose = "offers-fitted"
    handler = staticmethod(handlers.handle_offer_fitted)
