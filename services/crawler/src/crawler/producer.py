import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from confluent_kafka import Producer
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from jsonschema import FormatChecker, validators

from crawler.models import Observation, OutboxEvent

logger = logging.getLogger(__name__)

LISTINGS_TOPIC = "yadakchi.listings.observed.v2"
REVIEW_TOPIC = "yadakchi.review.requested.v1"
CLICKS_TOPIC = "yadakchi.clicks.recorded.v1"


class EventPublisher(Protocol):
    def publish(self, topic: str, key: str, body: Mapping[str, Any]) -> None: ...


class KafkaEventPublisher:
    def __init__(self, bootstrap_servers: str, client_id: str) -> None:
        self.producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "client.id": client_id,
                "enable.idempotence": True,
                "acks": "all",
                "compression.type": "gzip",
            }
        )

    def publish(self, topic: str, key: str, body: Mapping[str, Any]) -> None:
        delivery_error: list[Exception] = []

        def delivered(error: Exception | None, _: object) -> None:
            if error is not None:
                delivery_error.append(error)

        self.producer.produce(
            topic,
            key=key.encode("utf-8"),
            value=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            on_delivery=delivered,
        )
        remaining = self.producer.flush(30)
        if delivery_error:
            raise delivery_error[0]
        if remaining:
            raise TimeoutError(f"Kafka did not deliver {remaining} message(s) within 30 seconds")


def build_kafka_publisher() -> KafkaEventPublisher:
    return KafkaEventPublisher(settings.KAFKA_BOOTSTRAP_SERVERS, settings.KAFKA_CLIENT_ID)


def _schema_path(topic: str) -> Path:
    if topic == LISTINGS_TOPIC:
        return settings.BASE_DIR / "contracts/published/yadakchi.listings.observed.v2.json"
    if topic == REVIEW_TOPIC:
        return settings.BASE_DIR / "contracts/consumed/yadakchi.review.requested.v1.json"
    if topic == CLICKS_TOPIC:
        return settings.BASE_DIR / "contracts/consumed/yadakchi.clicks.recorded.v1.json"
    raise ValueError(f"No schema configured for topic {topic!r}")


def validate_event(topic: str, body: Mapping[str, Any]) -> None:
    schema = json.loads(_schema_path(topic).read_text(encoding="utf-8"))
    validator_class = validators.validator_for(schema)
    validator_class.check_schema(schema)
    validator_class(schema, format_checker=FormatChecker()).validate(dict(body))


def flush_outbox(publisher: EventPublisher, limit: int = 100) -> int:
    sent = 0
    while sent < limit:
        with transaction.atomic():
            event = (
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(sent_at__isnull=True)
                .order_by("created_at", "id")
                .first()
            )
            if event is None:
                break
            try:
                validate_event(event.topic, event.body)
                publisher.publish(event.topic, event.key, event.body)
            except Exception as exc:
                OutboxEvent.objects.filter(pk=event.pk).update(
                    attempts=F("attempts") + 1, last_error=str(exc)
                )
                logger.exception(
                    "outbox_publish_failed",
                    extra={"event_id": str(event.event_id), "topic": event.topic},
                )
                raise
            sent_at = timezone.now()
            OutboxEvent.objects.filter(pk=event.pk).update(
                attempts=F("attempts") + 1, last_error="", sent_at=sent_at
            )
            if event.observation_id is not None:
                Observation.objects.filter(pk=event.observation_id).update(emitted_at=sent_at)
            sent += 1
    return sent
