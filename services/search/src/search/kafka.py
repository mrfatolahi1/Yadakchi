from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol


class KafkaMessage(Protocol):
    def value(self) -> bytes | None: ...

    def key(self) -> bytes | None: ...


def decode_message(message: KafkaMessage) -> tuple[dict[str, Any], str | None]:
    value = message.value()
    if value is None:
        raise ValueError("Kafka message body may not be null; use an envelope tombstone")
    body = json.loads(value.decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("Kafka message body must be a JSON object")
    key_bytes = message.key()
    return body, key_bytes.decode("utf-8") if key_bytes is not None else None


def apply_then_commit(
    consumer: Any,
    message: KafkaMessage,
    handler: Callable[[dict[str, Any], str | None], object],
) -> None:
    body, key = decode_message(message)
    handler(body, key)
    consumer.commit(message=message, asynchronous=False)
