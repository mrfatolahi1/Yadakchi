"""Consume the vehicle tree from fitment."""

from __future__ import annotations

from django.conf import settings

from catalog.consumers import handlers
from catalog.management.commands._consumer_base import ConsumerCommand


class Command(ConsumerCommand):
    help = "Consume the vehicle tree from fitment."
    topic = settings.TOPIC_VEHICLES_CHANGED
    group_purpose = "vehicles-changed"
    handler = staticmethod(handlers.handle_vehicle_changed)
