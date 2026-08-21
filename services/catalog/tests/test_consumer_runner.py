"""The consumer loop's two guarantees.

1. **Offsets advance only after the work is durably written.** At-least-once
   plus idempotency, never at-most-once — losing an event because an offset
   moved early is unrecoverable, whereas replaying one is free.
2. **A poison message never wedges the topic.** It goes to the DLQ and the
   loop keeps moving.

The broker is faked, because what is under test is the loop's ordering, not
librdkafka.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from catalog import producer
from catalog.consumers.runner import ConsumerConfig, ConsumerRunner, parse_message
from catalog.events import Envelope

pytestmark = pytest.mark.django_db


class FakeMessage:
    def __init__(self, value: bytes | None, key: bytes | None = None) -> None:
        self._value = value
        self._key = key

    def value(self) -> bytes | None:
        return self._value

    def key(self) -> bytes | None:
        return self._key

    def error(self) -> None:
        return None


class FakeConsumer:
    """Records the order of everything the runner does to it."""

    def __init__(self) -> None:
        self.committed: list[Any] = []

    def commit(self, message: Any = None, asynchronous: bool = True) -> None:
        self.committed.append(message)


def envelope_bytes(**overrides: Any) -> bytes:
    body: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "event_type": "vehicles.changed",
        "version": 1,
        "occurred_at": "2026-08-19T07:00:00Z",
        "producer": "fitment",
        "trace_id": "trace-1",
        "payload": {
            "vehicle_slug": "peugeot-206-type-5",
            "brand": "Peugeot",
            "model": "206",
            "display_name_fa": "پژو ۲۰۶",
            "aliases": [],
            "is_published": True,
            "updated_at": "2026-08-19T07:00:00Z",
        },
    }
    body.update(overrides)
    return json.dumps(body).encode()


def _config(handler: Any) -> ConsumerConfig:
    return ConsumerConfig(
        topic="yadakchi.vehicles.changed.v1", group_purpose="vehicles-changed", handler=handler
    )


def test_the_offset_is_committed_only_after_the_handler_has_run() -> None:
    order: list[str] = []

    def handler(envelope: Envelope, topic: str, key: str | None) -> bool:
        order.append("handled")
        return True

    consumer = FakeConsumer()
    runner = ConsumerRunner(_config(handler))

    original_commit = consumer.commit

    def tracking_commit(message: Any = None, asynchronous: bool = True) -> None:
        order.append("committed")
        original_commit(message, asynchronous)

    consumer.commit = tracking_commit  # type: ignore[method-assign]
    runner._handle_message(consumer, FakeMessage(envelope_bytes()))

    assert order == ["handled", "committed"]
    assert len(consumer.committed) == 1


def test_a_handler_that_raises_does_not_advance_past_a_lost_write() -> None:
    """The message goes to the DLQ *before* the offset moves, so the event is
    recorded somewhere no matter what."""
    order: list[str] = []

    def handler(envelope: Envelope, topic: str, key: str | None) -> bool:
        raise RuntimeError("database is on fire")

    transport = producer.MemoryTransport()
    producer.set_transport(transport)

    consumer = FakeConsumer()
    original_commit = consumer.commit

    def tracking_commit(message: Any = None, asynchronous: bool = True) -> None:
        order.append("committed")
        original_commit(message, asynchronous)

    consumer.commit = tracking_commit  # type: ignore[method-assign]
    runner = ConsumerRunner(_config(handler))
    runner._handle_message(consumer, FakeMessage(envelope_bytes()))

    dlq = transport.for_topic("yadakchi.vehicles.changed.v1.dlq")
    assert len(dlq) == 1
    assert dlq[0].value is not None
    assert "database is on fire" in dlq[0].value["reason"]
    assert order == ["committed"]
    producer.set_transport(None)


def test_an_unparseable_message_goes_to_the_dlq_and_the_loop_continues() -> None:
    transport = producer.MemoryTransport()
    producer.set_transport(transport)

    def handler(envelope: Envelope, topic: str, key: str | None) -> bool:  # pragma: no cover
        raise AssertionError("must not be reached")

    consumer = FakeConsumer()
    runner = ConsumerRunner(_config(handler))
    runner._handle_message(consumer, FakeMessage(b"{not json at all"))

    assert len(transport.for_topic("yadakchi.vehicles.changed.v1.dlq")) == 1
    assert len(consumer.committed) == 1
    producer.set_transport(None)


def test_the_message_key_reaches_the_handler() -> None:
    """A tombstone has no payload, so the key is the only place the identity
    exists. It has to be passed through."""
    seen: list[str | None] = []

    def handler(envelope: Envelope, topic: str, key: str | None) -> bool:
        seen.append(key)
        return True

    runner = ConsumerRunner(_config(handler))
    runner._handle_message(FakeConsumer(), FakeMessage(envelope_bytes(), key=b"peugeot-206-type-5"))
    assert seen == ["peugeot-206-type-5"]


def test_auto_commit_is_off_and_replay_starts_at_the_beginning() -> None:
    """Two settings that decide whether reprocessing works at all."""
    import unittest.mock

    config = _config(lambda envelope, topic, key: True)
    runner = ConsumerRunner(config)
    with unittest.mock.patch("confluent_kafka.Consumer") as fake:
        runner._build_consumer()
    settings_used = fake.call_args[0][0]
    assert settings_used["enable.auto.commit"] is False
    assert settings_used["auto.offset.reset"] == "earliest"
    assert settings_used["group.id"] == "catalog.vehicles-changed"


def test_a_null_body_is_reported_rather_than_silently_dropped() -> None:
    with pytest.raises(ValueError, match="tombstone"):
        parse_message(None)


def test_a_vehicle_tombstone_flags_the_row_and_rebuilds(pipeline: Any) -> None:
    """End to end through the real handler, with the key doing the work."""
    from catalog.models import VehicleReadModel
    from tests.conftest import vehicle_payload

    pipeline.feed("vehicles.changed", vehicle_payload(), key="peugeot-206-type-5")
    assert VehicleReadModel.objects.get(vehicle_slug="peugeot-206-type-5").is_deleted is False

    pipeline.feed("vehicles.changed", None, key="peugeot-206-type-5")
    assert VehicleReadModel.objects.get(vehicle_slug="peugeot-206-type-5").is_deleted is True


def test_a_tombstone_without_a_key_is_refused(pipeline: Any) -> None:
    """Better to log and skip than to guess which entity was deleted."""
    assert pipeline.feed("vehicles.changed", None, key=None) is False
