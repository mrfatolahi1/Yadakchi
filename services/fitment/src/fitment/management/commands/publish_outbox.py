from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandParser

from fitment.producer import KafkaOutboxPublisher


class Command(BaseCommand):
    help = "Relay durable outbox rows to Kafka."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--follow", action="store_true")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args: object, **options: object) -> None:
        del args
        publisher = KafkaOutboxPublisher()
        limit = options.get("limit")
        follow = options.get("follow")
        if not isinstance(limit, int) or not isinstance(follow, bool):
            raise ValueError("Invalid outbox command options.")
        while True:
            count = publisher.publish_pending(limit=limit)
            if not follow:
                return
            if count == 0:
                time.sleep(1)
