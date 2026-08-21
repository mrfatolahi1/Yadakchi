"""The Kafka consumer loop.

Two properties matter more than anything else here, and both are structural
rather than incidental:

**Offsets are committed only after the work is durably written.** Auto-commit
is off. The handler runs inside a database transaction; the offset is
committed after that transaction has landed. A crash in between replays the
message, which is safe precisely because the handlers are idempotent — this
is at-least-once with an idempotency guard, never at-most-once.

**A poison message never blocks the topic.** A message we cannot parse or
handle goes to the DLQ companion topic and the loop moves on. Losing one
malformed message is bad; wedging the whole pipeline behind it is worse.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from pydantic import ValidationError

from catalog import producer
from catalog.events import Envelope
from catalog.metrics import EVENTS_CONSUMED, EVENTS_FAILED, EVENTS_SKIPPED

logger = logging.getLogger("catalog.consumers.runner")

Handler = Callable[[Envelope, str, str | None], bool]


@dataclass
class ConsumerConfig:
    topic: str
    #: "<service>.<purpose>", never shared across services.
    group_purpose: str
    handler: Handler
    dlq_suffix: str = ".dlq"
    poll_timeout: float = 1.0
    #: Stop after this many messages. Only tests and one-shot replays set it.
    max_messages: int | None = None

    @property
    def group_id(self) -> str:
        return f"{settings.KAFKA_CONSUMER_GROUP_PREFIX}.{self.group_purpose}"

    @property
    def dlq_topic(self) -> str:
        return f"{self.topic}{self.dlq_suffix}"


class ConsumerRunner:
    """Runs one topic. One process per topic, one consumer group per purpose."""

    def __init__(self, config: ConsumerConfig) -> None:
        self.config = config
        self._running = True
        self._consumed = 0

    def stop(self, *_: object) -> None:
        logger.info("shutdown requested", extra={"topic": self.config.topic})
        self._running = False

    def _build_consumer(self) -> Any:
        from confluent_kafka import Consumer

        return Consumer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "security.protocol": settings.KAFKA_SECURITY_PROTOCOL,
                "group.id": self.config.group_id,
                # Replay means "from the beginning" — a new group rebuilds the
                # whole read model rather than starting at the live edge.
                "auto.offset.reset": "earliest",
                # The whole point: we decide when an offset is safe.
                "enable.auto.commit": False,
                "max.poll.interval.ms": 600000,
            }
        )

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        consumer = self._build_consumer()
        consumer.subscribe([self.config.topic])
        logger.info(
            "consumer started",
            extra={"topic": self.config.topic, "group_id": self.config.group_id},
        )

        try:
            while self._running:
                message = consumer.poll(self.config.poll_timeout)
                if message is None:
                    continue
                if message.error():
                    logger.error(
                        "kafka error",
                        extra={"topic": self.config.topic, "error": str(message.error())},
                    )
                    continue

                self._handle_message(consumer, message)
                self._consumed += 1
                if self.config.max_messages and self._consumed >= self.config.max_messages:
                    break
        finally:
            consumer.close()
            producer.flush()
            logger.info(
                "consumer stopped",
                extra={"topic": self.config.topic, "messages": self._consumed},
            )
        return self._consumed

    def _handle_message(self, consumer: Any, message: Any) -> None:
        topic = self.config.topic
        key = message.key().decode("utf-8") if message.key() else None
        raw = message.value()

        try:
            envelope = parse_message(raw)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            EVENTS_FAILED.labels(topic=topic, reason="unparseable").inc()
            self._to_dlq(message, f"unparseable: {exc}")
            consumer.commit(message=message, asynchronous=False)
            return

        try:
            applied = self.config.handler(envelope, topic, key)
        except Exception as exc:
            EVENTS_FAILED.labels(topic=topic, reason=type(exc).__name__).inc()
            logger.exception(
                "handler failed",
                extra={
                    "topic": topic,
                    "event_id": str(envelope.event_id),
                    "trace_id": envelope.trace_id,
                    "key": key,
                },
            )
            self._to_dlq(message, f"handler error: {exc}")
            consumer.commit(message=message, asynchronous=False)
            return

        if applied:
            EVENTS_CONSUMED.labels(topic=topic).inc()
        else:
            EVENTS_SKIPPED.labels(topic=topic).inc()

        # The handler's transaction has committed by now. Only now is the
        # offset safe to advance.
        consumer.commit(message=message, asynchronous=False)

    def _to_dlq(self, message: Any, reason: str) -> None:
        transport = producer.get_transport()
        transport.send(
            producer.OutboundEvent(
                topic=self.config.dlq_topic,
                key=(message.key() or b"").decode("utf-8", "replace"),
                value={
                    "reason": reason,
                    "topic": self.config.topic,
                    "raw": (message.value() or b"").decode("utf-8", "replace")[:8192],
                },
            )
        )
        logger.error("message sent to dlq", extra={"topic": self.config.topic, "reason": reason})


def parse_message(raw: bytes | None) -> Envelope:
    """Decode one Kafka message body into an envelope.

    A ``None`` body is a compaction tombstone, which is a legitimate message
    on a compacted topic and not an error — but it carries no envelope, so
    there is nothing here to parse and the caller must fall back to the key.
    """
    if raw is None:
        raise ValueError("tombstone with no envelope: compaction deleted the record")
    document = json.loads(raw.decode("utf-8"))
    return Envelope.model_validate(document)


def run_consumer(config: ConsumerConfig) -> int:
    try:
        return ConsumerRunner(config).run()
    except KeyboardInterrupt:  # pragma: no cover
        return 0
    except Exception:  # pragma: no cover
        logger.exception("consumer crashed", extra={"topic": config.topic})
        sys.exit(1)
