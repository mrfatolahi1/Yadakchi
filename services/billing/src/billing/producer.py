from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Protocol

from confluent_kafka import KafkaError, Message, Producer
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from billing.models import OutboxEvent

logger = logging.getLogger(__name__)


class Publisher(Protocol):
    def publish(self, *, topic: str, key: str, body: dict[str, object]) -> None: ...


class KafkaPublisher:
    def __init__(self) -> None:
        self._producer = Producer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "enable.idempotence": True,
                "acks": "all",
                "compression.type": "snappy",
            }
        )

    def publish(self, *, topic: str, key: str, body: dict[str, object]) -> None:
        delivery_error: list[KafkaError] = []

        def delivered(error: KafkaError | None, _message: Message) -> None:
            if error is not None:
                delivery_error.append(error)

        self._producer.produce(
            topic=topic,
            key=key.encode(),
            value=json.dumps(body, separators=(",", ":")).encode(),
            on_delivery=delivered,
        )
        remaining = self._producer.flush(10)
        if remaining or delivery_error:
            detail = str(delivery_error[0]) if delivery_error else "producer flush timed out"
            raise RuntimeError(detail)


def publish_pending(
    publisher: Publisher | None = None,
    *,
    limit: int = 100,
    event_filter: Callable[[OutboxEvent], bool] | None = None,
) -> int:
    events = list(OutboxEvent.objects.filter(published_at__isnull=True)[:limit])
    if not events:
        return 0
    active_publisher = publisher or KafkaPublisher()
    published = 0
    for event in events:
        if event_filter is not None and not event_filter(event):
            continue
        OutboxEvent.objects.filter(event_id=event.event_id).update(
            publish_attempts=F("publish_attempts") + 1
        )
        try:
            active_publisher.publish(
                topic=event.topic,
                key=event.message_key,
                body=event.body,
            )
        except Exception as exc:
            OutboxEvent.objects.filter(event_id=event.event_id).update(last_error=str(exc))
            logger.exception(
                "outbox publish failed",
                extra={"event": "outbox_publish_failed", "topic": event.topic},
            )
            continue
        updated = OutboxEvent.objects.filter(
            event_id=event.event_id, published_at__isnull=True
        ).update(published_at=timezone.now(), last_error="")
        published += updated
    return published
