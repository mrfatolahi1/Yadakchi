"""Shared scaffolding for the consume_* management commands.

A Kafka consumer is a long-running management command, not a Celery task:
offsets belong to the process that does the work, and a task queue that can
retry a message elsewhere would make them meaningless.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from catalog.consumers.runner import ConsumerConfig, Handler, run_consumer


class ConsumerCommand(BaseCommand):
    """Base for one topic. Subclasses set the three class attributes."""

    topic: str = ""
    group_purpose: str = ""
    handler: Handler

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--max-messages",
            type=int,
            default=None,
            help="Stop after N messages. For one-shot replays and smoke tests.",
        )
        parser.add_argument(
            "--poll-timeout",
            type=float,
            default=1.0,
            help="Seconds to block on each poll.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        config = ConsumerConfig(
            topic=self.topic,
            group_purpose=self.group_purpose,
            handler=type(self).handler,
            max_messages=options.get("max_messages"),
            poll_timeout=options.get("poll_timeout", 1.0),
        )
        run_consumer(config)
