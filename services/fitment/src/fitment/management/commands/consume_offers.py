from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from fitment.consumers import consume_forever, process_offer_event


class Command(BaseCommand):
    help = "Consume enriched offers with manual offset commits after durable writes."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        consume_forever(
            topic="yadakchi.offers.enriched.v1",
            group_id=f"{settings.KAFKA_GROUP_PREFIX}-offers-v1",
            processor=process_offer_event,
        )
