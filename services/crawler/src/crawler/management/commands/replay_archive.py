from datetime import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.utils.dateparse import parse_datetime

from crawler.models import Source
from crawler.producer import build_kafka_publisher
from crawler.replay import replay_observations


def optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError(f"Invalid ISO-8601 timestamp: {value}")
    return parsed


class Command(BaseCommand):
    help = "Replay archived listing observations without fetching seller sites"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--since")
        parser.add_argument("--until")
        parser.add_argument("--rate", type=float, default=50.0)
        parser.add_argument("--reset", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        source = Source.objects.get(key=options["source"])
        emitted = replay_observations(
            source,
            build_kafka_publisher(),
            since=optional_datetime(options["since"]),
            until=optional_datetime(options["until"]),
            rate_per_second=options["rate"],
            reset=options["reset"],
        )
        self.stdout.write(self.style.SUCCESS(f"emitted={emitted}"))
