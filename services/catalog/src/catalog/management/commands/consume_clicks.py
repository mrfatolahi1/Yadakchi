"""Consume outbound clicks from billing."""

from __future__ import annotations

from django.conf import settings

from catalog.consumers import handlers
from catalog.management.commands._consumer_base import ConsumerCommand


class Command(ConsumerCommand):
    help = "Consume outbound clicks from billing."
    topic = settings.TOPIC_CLICKS_RECORDED
    group_purpose = "clicks-recorded"
    handler = staticmethod(handlers.handle_click_recorded)
