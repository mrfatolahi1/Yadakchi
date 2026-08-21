"""Publishing ``products.changed.v1`` and ``sellers.changed.v1``.

Both topics are compacted and keyed by identity, so the last message on a key
is the whole truth about that entity. That is why the product payload is the
largest in the system: `search` and `web` render from it and must never call
back here to fill a gap.

Emission is **debounced**. A burst of material changes — a recrawl touching
every offer in a cluster — produces one event, not one per change, and a
rebuild that changes nothing produces none at all.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.conf import settings

from catalog.events import iso_utc
from catalog.metrics import PRODUCTS_EMITTED, SELLERS_EMITTED

logger = logging.getLogger("catalog.producer")


@dataclass(frozen=True)
class OutboundEvent:
    topic: str
    key: str
    value: dict[str, Any] | None


class Transport(Protocol):
    """Where an event actually goes."""

    def send(self, event: OutboundEvent) -> None: ...

    def flush(self, timeout: float = 10.0) -> None: ...


@dataclass
class MemoryTransport:
    """Collects events in process. Used by tests and by dry runs."""

    sent: list[OutboundEvent] = field(default_factory=list)

    def send(self, event: OutboundEvent) -> None:
        self.sent.append(event)

    def flush(self, timeout: float = 10.0) -> None:
        return None

    def clear(self) -> None:
        self.sent.clear()

    def for_topic(self, topic: str) -> list[OutboundEvent]:
        return [e for e in self.sent if e.topic == topic]


class KafkaTransport:
    """confluent-kafka producer, configured for durability over latency."""

    def __init__(self) -> None:
        from confluent_kafka import Producer

        self._producer = Producer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "security.protocol": settings.KAFKA_SECURITY_PROTOCOL,
                "enable.idempotence": True,
                "acks": "all",
                "compression.type": "zstd",
                "linger.ms": 50,
            }
        )

    def send(self, event: OutboundEvent) -> None:
        payload = (
            None
            if event.value is None
            else json.dumps(event.value, ensure_ascii=False).encode("utf-8")
        )
        self._producer.produce(topic=event.topic, key=event.key.encode("utf-8"), value=payload)
        self._producer.poll(0)

    def flush(self, timeout: float = 10.0) -> None:
        remaining = self._producer.flush(timeout)
        if remaining:
            raise RuntimeError(f"kafka flush left {remaining} message(s) unsent")


_transport: Transport | None = None


def get_transport() -> Transport:
    """The process-wide transport. ``CATALOG_EVENT_TRANSPORT=memory`` keeps
    everything in process, which is what tests run against."""
    global _transport
    if _transport is None:
        _transport = MemoryTransport() if settings.EVENT_TRANSPORT == "memory" else KafkaTransport()
    return _transport


def set_transport(transport: Transport | None) -> None:
    """Swap the transport. Tests use this; nothing else should."""
    global _transport
    _transport = transport


def envelope(
    event_type: str, payload: dict[str, Any] | None, *, trace_id: str, now: dt.datetime
) -> dict[str, Any]:
    """The shared envelope every topic carries.

    ``event_id`` is fresh per emission by design — the contract is explicit
    that consumers deduplicate on the payload's natural identity, never on
    this value.
    """
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "occurred_at": iso_utc(now),
        "producer": settings.SERVICE_NAME,
        "trace_id": trace_id,
        "payload": payload,
    }


def new_trace_id() -> str:
    """A chain that starts here — a scheduled recomputation, an admin edit —
    mints its own trace id. A chain that starts upstream carries theirs."""
    return uuid.uuid4().hex[:16]


def publish_product(payload: dict[str, Any], *, trace_id: str, now: dt.datetime) -> OutboundEvent:
    event = OutboundEvent(
        topic=settings.TOPIC_PRODUCTS_CHANGED,
        key=str(payload["product_uid"]),
        value=envelope("products.changed", payload, trace_id=trace_id, now=now),
    )
    get_transport().send(event)
    PRODUCTS_EMITTED.inc()
    logger.info(
        "products.changed emitted",
        extra={
            "product_uid": payload["product_uid"],
            "offer_count": payload.get("offer_count"),
            "is_published": payload.get("is_published"),
            "trace_id": trace_id,
        },
    )
    return event


def publish_seller(payload: dict[str, Any], *, trace_id: str, now: dt.datetime) -> OutboundEvent:
    event = OutboundEvent(
        topic=settings.TOPIC_SELLERS_CHANGED,
        key=str(payload["seller_key"]),
        value=envelope("sellers.changed", payload, trace_id=trace_id, now=now),
    )
    get_transport().send(event)
    SELLERS_EMITTED.inc()
    logger.info(
        "sellers.changed emitted",
        extra={
            "seller_key": payload["seller_key"],
            "tier": payload.get("tier"),
            "trust_score": payload.get("trust_score"),
            "trace_id": trace_id,
        },
    )
    return event


def flush(timeout: float = 10.0) -> None:
    get_transport().flush(timeout)
