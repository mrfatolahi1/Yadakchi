from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from confluent_kafka import Consumer, KafkaError, Message
from django.conf import settings
from django.db import transaction
from pydantic import ValidationError

from fitment.crossref import infer_shared_fitment_crossrefs, infer_title_crossrefs
from fitment.inference import offer_group_key, recompute_group
from fitment.models import (
    DeadLetterEvent,
    FitmentStatus,
    HumanCorrection,
    OfferReadModel,
    ProcessedEvent,
    ReviewRequestState,
    Vehicle,
)
from fitment.producer import stable_hash
from fitment.schemas import EnrichedOfferPayload, EventEnvelope, ReviewDecisionPayload
from fitment.text import normalize_part_number

logger = logging.getLogger(__name__)


def _record_processed(envelope: EventEnvelope, *, topic: str, natural_key: str) -> None:
    ProcessedEvent.objects.create(
        event_id=envelope.event_id,
        topic=topic,
        natural_key=natural_key,
        occurred_at=envelope.occurred_at,
    )


def _dead_letter(*, event_id: str, topic: str, reason: str, message: dict[str, Any]) -> None:
    DeadLetterEvent.objects.update_or_create(
        event_id=event_id,
        defaults={"topic": topic, "reason": reason, "message": message},
    )


@transaction.atomic
def process_offer_event(message: dict[str, Any]) -> bool:
    envelope = EventEnvelope.model_validate(message)
    if envelope.event_type != "offers.enriched" or envelope.producer != "enricher":
        raise ValueError("Unexpected event type or producer for offers consumer.")
    if ProcessedEvent.objects.filter(event_id=envelope.event_id).exists():
        return False
    if envelope.payload is None:
        raise ValueError("offers.enriched does not permit tombstones.")
    payload = EnrichedOfferPayload.model_validate(envelope.payload)
    fingerprint = stable_hash(envelope.payload)
    normalized_part_number = normalize_part_number(payload.part_number)
    values: dict[str, Any] = {
        "source_key": payload.source_key,
        "external_key": payload.external_key,
        "seller_key": payload.seller_key,
        "url": payload.url,
        "raw_title": payload.raw_title,
        "title_normalized": payload.title_normalized,
        "brand": payload.brand,
        "part_number": normalized_part_number,
        "part_type": payload.part_type,
        "price_toman": payload.price_toman,
        "vehicle_hints": payload.vehicle_hints,
        "vehicle_hints_excluded": payload.vehicle_hints_excluded,
        "overbroad_claim": payload.overbroad_claim,
        "is_active": payload.is_active,
        "group_key": offer_group_key(
            {
                "offer_uid": payload.offer_uid,
                "part_number": normalized_part_number,
                "brand": payload.brand,
                "part_type": payload.part_type,
            }
        ),
        "trace_id": envelope.trace_id,
        "source_occurred_at": envelope.occurred_at,
        "payload_hash": fingerprint,
    }
    existing = (
        OfferReadModel.objects.select_for_update().filter(offer_uid=payload.offer_uid).first()
    )
    if existing and envelope.occurred_at < existing.source_occurred_at:
        _record_processed(
            envelope, topic="yadakchi.offers.enriched.v1", natural_key=payload.offer_uid
        )
        return False
    if existing and existing.payload_hash == fingerprint:
        _record_processed(
            envelope, topic="yadakchi.offers.enriched.v1", natural_key=payload.offer_uid
        )
        return False

    old_group_key = existing.group_key if existing else None
    if existing is None:
        offer = OfferReadModel.objects.create(offer_uid=payload.offer_uid, **values)
    else:
        offer = existing
        for field, value in values.items():
            setattr(offer, field, value)
        offer.save()
    _record_processed(envelope, topic="yadakchi.offers.enriched.v1", natural_key=payload.offer_uid)

    infer_title_crossrefs(offer)
    affected_groups = {offer.group_key}
    if old_group_key and old_group_key != offer.group_key:
        affected_groups.add(old_group_key)
    for group_key in affected_groups:
        recompute_group(group_key, trace_id=envelope.trace_id)
    infer_shared_fitment_crossrefs(offer.part_type)
    return True


@transaction.atomic
def process_decision_event(message: dict[str, Any], *, message_key: str | None = None) -> bool:
    envelope = EventEnvelope.model_validate(message)
    if envelope.event_type != "review.decided" or envelope.producer != "ops":
        raise ValueError("Unexpected event type or producer for decisions consumer.")
    if ProcessedEvent.objects.filter(event_id=envelope.event_id).exists():
        return False
    if envelope.payload is None:
        if message_key:
            correction = HumanCorrection.objects.filter(request_uid=message_key).first()
            part_number = correction.part_number if correction else None
            HumanCorrection.objects.filter(request_uid=message_key).delete()
            ReviewRequestState.objects.filter(request_uid=message_key).update(
                state=ReviewRequestState.State.SKIPPED
            )
            if part_number:
                for group_key in (
                    OfferReadModel.objects.filter(part_number=part_number)
                    .values_list("group_key", flat=True)
                    .distinct()
                ):
                    recompute_group(group_key, trace_id=envelope.trace_id)
        _record_processed(
            envelope,
            topic="yadakchi.review.decided.v1",
            natural_key=message_key or str(envelope.event_id),
        )
        return True

    payload = ReviewDecisionPayload.model_validate(envelope.payload)
    if payload.kind != "fitment_conflict":
        _record_processed(
            envelope, topic="yadakchi.review.decided.v1", natural_key=payload.request_uid
        )
        return False
    if payload.decision == "skip":
        ReviewRequestState.objects.filter(request_uid=payload.request_uid).update(
            state=ReviewRequestState.State.SKIPPED
        )
        _record_processed(
            envelope, topic="yadakchi.review.decided.v1", natural_key=payload.request_uid
        )
        return True
    if payload.decision != "approve":
        reason = f"Invalid fitment_conflict decision: {payload.decision}"
        _dead_letter(
            event_id=str(envelope.event_id),
            topic="yadakchi.review.decided.v1",
            reason=reason,
            message=message,
        )
        _record_processed(
            envelope, topic="yadakchi.review.decided.v1", natural_key=payload.request_uid
        )
        return False

    part_number = normalize_part_number(str(payload.subject.get("part_number", "")))
    vehicle_slug = str(payload.subject.get("vehicle_slug", ""))
    status = str(payload.subject.get("status", ""))
    if (
        part_number is None
        or status not in FitmentStatus.values
        or not Vehicle.objects.filter(slug=vehicle_slug).exists()
    ):
        reason = (
            "fitment_conflict approval requires part_number, known vehicle_slug, "
            "and tri-state status"
        )
        _dead_letter(
            event_id=str(envelope.event_id),
            topic="yadakchi.review.decided.v1",
            reason=reason,
            message=message,
        )
        _record_processed(
            envelope, topic="yadakchi.review.decided.v1", natural_key=payload.request_uid
        )
        return False

    HumanCorrection.objects.update_or_create(
        part_number=part_number,
        vehicle_id=vehicle_slug,
        defaults={
            "request_uid": payload.request_uid,
            "status": status,
            "actor": payload.actor,
            "reason": payload.reason,
            "decided_at": payload.decided_at,
            "trace_id": envelope.trace_id,
        },
    )
    ReviewRequestState.objects.filter(part_number=part_number, vehicle_id=vehicle_slug).update(
        state=ReviewRequestState.State.SETTLED, request_uid=payload.request_uid
    )
    _record_processed(envelope, topic="yadakchi.review.decided.v1", natural_key=payload.request_uid)
    for group_key in (
        OfferReadModel.objects.filter(part_number=part_number)
        .values_list("group_key", flat=True)
        .distinct()
    ):
        recompute_group(group_key, trace_id=envelope.trace_id)
    return True


def consume_forever(
    *,
    topic: str,
    group_id: str,
    processor: Callable[..., bool],
) -> None:
    consumer = Consumer(
        {
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "client.id": group_id,
        }
    )
    consumer.subscribe([topic])
    try:
        while True:
            kafka_message = consumer.poll(1.0)
            if kafka_message is None:
                continue
            error = kafka_message.error()
            if error is not None:
                if error.code() == KafkaError._PARTITION_EOF:
                    continue
                raise RuntimeError(str(error))
            _process_kafka_message(topic, processor, kafka_message)
            consumer.commit(message=kafka_message, asynchronous=False)
    finally:
        consumer.close()


def _process_kafka_message(
    topic: str, processor: Callable[..., bool], kafka_message: Message
) -> None:
    raw = kafka_message.value() or b""
    raw_key = kafka_message.key()
    key = raw_key.decode() if raw_key else None
    try:
        decoded = json.loads(raw.decode())
        if topic == "yadakchi.review.decided.v1":
            processor(decoded, message_key=key)
        else:
            processor(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError) as exc:
        event_id = stable_hash(raw.decode(errors="replace"))
        with transaction.atomic():
            _dead_letter(
                event_id=event_id,
                topic=topic,
                reason=str(exc),
                message={"raw": raw.decode(errors="replace"), "key": key},
            )
        logger.exception(
            "consumer_message_dead_lettered",
            extra={"trace_id": "unknown", "topic": topic, "event_id": event_id},
        )
