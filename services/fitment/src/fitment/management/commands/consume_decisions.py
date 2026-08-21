from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from fitment.consumers import consume_forever, process_decision_event


class Command(BaseCommand):
    help = "Consume sticky human fitment decisions."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        consume_forever(
            topic="yadakchi.review.decided.v1",
            group_id=f"{settings.KAFKA_GROUP_PREFIX}-decisions-v1",
            processor=process_decision_event,
        )
