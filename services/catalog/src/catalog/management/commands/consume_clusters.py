"""Consume cluster memberships from matcher."""

from __future__ import annotations

from django.conf import settings

from catalog.consumers import handlers
from catalog.management.commands._consumer_base import ConsumerCommand


class Command(ConsumerCommand):
    help = "Consume cluster memberships from matcher."
    topic = settings.TOPIC_CLUSTERS_CHANGED
    group_purpose = "clusters-changed"
    handler = staticmethod(handlers.handle_cluster_changed)
