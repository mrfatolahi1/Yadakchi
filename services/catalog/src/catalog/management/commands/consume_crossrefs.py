"""Consume cross-reference pairs from fitment."""

from __future__ import annotations

from django.conf import settings

from catalog.consumers import handlers
from catalog.management.commands._consumer_base import ConsumerCommand


class Command(ConsumerCommand):
    help = "Consume cross-reference pairs from fitment."
    topic = settings.TOPIC_CROSSREFS_CHANGED
    group_purpose = "crossrefs-changed"
    handler = staticmethod(handlers.handle_crossref_changed)
