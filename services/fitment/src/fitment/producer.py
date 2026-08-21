from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from confluent_kafka import Producer
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from fitment.models import CrossRef, OutboxEvent, Vehicle

logger = logging.getLogger(__name__)

OFFERS_FITTED_TOPIC = "yadakchi.offers.fitted.v1"
VEHICLES_CHANGED_TOPIC = "yadakchi.vehicles.changed.v1"
CROSSREFS_CHANGED_TOPIC = "yadakchi.crossrefs.changed.v1"
REVIEW_REQUESTED_TOPIC = "yadakchi.review.requested.v1"


def utc_iso(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    return current.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def stable_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def build_envelope(
    *, event_type: str, payload: dict[str, Any] | None, trace_id: str, event_id: UUID | None = None
) -> dict[str, Any]:
    return {
        "event_id": str(event_id or uuid4()),
        "event_type": event_type,
        "version": 1,
        "occurred_at": utc_iso(),
        "producer": "fitment",
        "trace_id": trace_id,
        "payload": payload,
    }


def queue_event(
    *,
    topic: str,
    event_type: str,
    message_key: str,
    payload: dict[str, Any] | None,
    trace_id: str,
    dedupe_key: str,
) -> OutboxEvent:
    event_id = uuid4()
    event, _ = OutboxEvent.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "event_id": event_id,
            "topic": topic,
            "message_key": message_key,
            "envelope": build_envelope(
                event_type=event_type, payload=payload, trace_id=trace_id, event_id=event_id
            ),
            "trace_id": trace_id,
        },
    )
    return event


def vehicle_payload(vehicle: Vehicle) -> dict[str, Any]:
    return {
        "vehicle_slug": vehicle.slug,
        "brand": vehicle.brand,
        "model": vehicle.model,
        "trim": vehicle.trim,
        "year_from": vehicle.year_from,
        "year_to": vehicle.year_to,
        "engine_code": vehicle.engine_code,
        "display_name_fa": vehicle.display_name_fa,
        "aliases": vehicle.aliases,
        "is_published": vehicle.is_published,
        "updated_at": utc_iso(vehicle.updated_at),
    }


def queue_vehicle_changed(vehicle: Vehicle, *, trace_id: str, force_token: str = "") -> OutboxEvent:
    payload = vehicle_payload(vehicle)
    return queue_event(
        topic=VEHICLES_CHANGED_TOPIC,
        event_type="vehicles.changed",
        message_key=vehicle.slug,
        payload=payload,
        trace_id=trace_id,
        dedupe_key=f"vehicle:{vehicle.slug}:{stable_hash(payload)}:{force_token}",
    )


def queue_vehicle_tombstone(slug: str, *, trace_id: str) -> OutboxEvent:
    return queue_event(
        topic=VEHICLES_CHANGED_TOPIC,
        event_type="vehicles.changed",
        message_key=slug,
        payload=None,
        trace_id=trace_id,
        dedupe_key=f"vehicle:{slug}:tombstone:{uuid4().hex}",
    )


def crossref_payload(crossref: CrossRef) -> dict[str, Any]:
    return {
        "code_a": crossref.code_a,
        "code_b": crossref.code_b,
        "brand_a": crossref.brand_a,
        "brand_b": crossref.brand_b,
        "confidence": crossref.confidence,
        "provenance": crossref.provenance,
        "updated_at": utc_iso(crossref.updated_at),
    }


def queue_crossref_changed(
    crossref: CrossRef, *, trace_id: str, force_token: str = ""
) -> OutboxEvent:
    payload = crossref_payload(crossref)
    key = f"{crossref.code_a}|{crossref.code_b}"
    return queue_event(
        topic=CROSSREFS_CHANGED_TOPIC,
        event_type="crossrefs.changed",
        message_key=key,
        payload=payload,
        trace_id=trace_id,
        dedupe_key=f"crossref:{key}:{stable_hash(payload)}:{force_token}",
    )


def queue_crossref_tombstone(code_a: str, code_b: str, *, trace_id: str) -> OutboxEvent:
    first, second = sorted((code_a, code_b))
    key = f"{first}|{second}"
    return queue_event(
        topic=CROSSREFS_CHANGED_TOPIC,
        event_type="crossrefs.changed",
        message_key=key,
        payload=None,
        trace_id=trace_id,
        dedupe_key=f"crossref:{key}:tombstone:{uuid4().hex}",
    )


def queue_fitted(
    *, offer_uid: str, payload: dict[str, Any], semantic_hash: str, trace_id: str
) -> OutboxEvent:
    return queue_event(
        topic=OFFERS_FITTED_TOPIC,
        event_type="offers.fitted",
        message_key=offer_uid,
        payload=payload,
        trace_id=trace_id,
        dedupe_key=f"fitted:{offer_uid}:{semantic_hash}",
    )


def queue_review_request(
    *, request_uid: str, subject: dict[str, Any], evidence: dict[str, Any], trace_id: str
) -> OutboxEvent:
    payload = {
        "request_uid": request_uid,
        "kind": "fitment_conflict",
        "priority": 50,
        "subject": subject,
        "evidence": evidence,
        "requested_at": utc_iso(),
    }
    return queue_event(
        topic=REVIEW_REQUESTED_TOPIC,
        event_type="review.requested",
        message_key=request_uid,
        payload=payload,
        trace_id=trace_id,
        dedupe_key=f"review:{request_uid}",
    )


class KafkaOutboxPublisher:
    def __init__(self) -> None:
        self.producer = Producer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "enable.idempotence": True,
                "acks": "all",
                "client.id": "fitment-outbox",
            }
        )

    def publish_pending(self, *, limit: int = 100) -> int:
        published = 0
        event_ids = list(
            OutboxEvent.objects.filter(published_at__isnull=True)
            .order_by("created_at")
            .values_list("event_id", flat=True)[:limit]
        )
        for event_id in event_ids:
            with transaction.atomic():
                event = OutboxEvent.objects.select_for_update().get(event_id=event_id)
                if event.published_at is not None:
                    continue
                event.publish_attempts += 1
                event.save(update_fields=["publish_attempts"])
                self.producer.produce(
                    event.topic,
                    key=event.message_key.encode(),
                    value=json.dumps(event.envelope, ensure_ascii=False).encode(),
                )
                self.producer.flush()
                event.published_at = timezone.now()
                event.save(update_fields=["published_at"])
                published += 1
                logger.info(
                    "outbox_event_published",
                    extra={
                        "trace_id": event.trace_id,
                        "topic": event.topic,
                        "event_id": str(event_id),
                    },
                )
        return published
