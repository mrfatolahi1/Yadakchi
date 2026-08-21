import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from confluent_kafka import Consumer, KafkaError, Message
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from crawler.events import ClickRecordedEvent
from crawler.models import ClickSignal, ConsumedClick, Observation
from crawler.producer import CLICKS_TOPIC, validate_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClickConsumeResult:
    created: bool
    matched: bool


@transaction.atomic
def consume_click_event(
    event: ClickRecordedEvent, now: datetime | None = None
) -> ClickConsumeResult:
    payload = event.payload
    matched = Observation.objects.filter(offer_uid=payload.offer_uid).exists()
    _, created = ConsumedClick.objects.get_or_create(
        click_id=payload.click_id,
        defaults={
            "event_id": event.event_id,
            "offer_uid": payload.offer_uid,
            "occurred_at": payload.occurred_at,
            "matched": matched,
        },
    )
    if not created:
        return ClickConsumeResult(False, False)
    if matched:
        refresh_click_signal(payload.offer_uid, now=now)
    return ClickConsumeResult(True, matched)


def refresh_click_signal(offer_uid: str, now: datetime | None = None) -> ClickSignal:
    measured_at = now or timezone.now()
    count = ConsumedClick.objects.filter(
        offer_uid=offer_uid,
        matched=True,
        occurred_at__gte=measured_at - timedelta(days=7),
        occurred_at__lte=measured_at,
    ).count()
    signal, _ = ClickSignal.objects.update_or_create(
        offer_uid=offer_uid, defaults={"count_7d": count, "updated_at": measured_at}
    )
    return signal


def parse_click_message(raw: bytes) -> ClickRecordedEvent:
    body: dict[str, Any] = json.loads(raw)
    validate_event(CLICKS_TOPIC, body)
    return ClickRecordedEvent.model_validate(body)


class ConsumerLike(Protocol):
    def poll(self, timeout: float) -> Message | None: ...

    def commit(self, message: Message, asynchronous: bool = False) -> Any: ...


class ClickConsumerRunner:
    def __init__(self, consumer: ConsumerLike) -> None:
        self.consumer = consumer

    def run_once(self, timeout: float = 1.0) -> bool:
        message = self.consumer.poll(timeout)
        if message is None:
            return False
        error = message.error()
        if error is not None:
            if error.code() == KafkaError._PARTITION_EOF:
                return False
            raise RuntimeError(str(error))
        event = parse_click_message(bytes(message.value()))
        result = consume_click_event(event)
        # The transaction in consume_click_event has committed before the offset moves.
        self.consumer.commit(message=message, asynchronous=False)
        logger.info(
            "click_consumed",
            extra={"event_id": str(event.event_id), "trace_id": event.trace_id},
        )
        return result.created


def build_click_consumer() -> Consumer:
    consumer = Consumer(
        {
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": settings.KAFKA_CLICK_GROUP_ID,
            "client.id": f"{settings.KAFKA_CLIENT_ID}-clicks",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([CLICKS_TOPIC])
    return consumer
