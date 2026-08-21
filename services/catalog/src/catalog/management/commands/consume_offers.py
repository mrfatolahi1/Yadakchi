"""Consume enriched offers from enricher."""

from __future__ import annotations

from django.conf import settings

from catalog.consumers import handlers
from catalog.management.commands._consumer_base import ConsumerCommand


class Command(ConsumerCommand):
    help = "Consume enriched offers from enricher."
    topic = settings.TOPIC_OFFERS_ENRICHED
    group_purpose = "offers-enriched"
    handler = staticmethod(handlers.handle_offer_enriched)
