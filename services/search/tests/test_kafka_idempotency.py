from __future__ import annotations

import json
from typing import Any

from search.kafka import apply_then_commit


class Message:
    def value(self) -> bytes:
        return json.dumps({"event_type": "products.changed"}).encode()

    def key(self) -> bytes:
        return b"product-1"


class Consumer:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    def commit(self, *, message: Any, asynchronous: bool = False) -> Any:
        del message
        assert asynchronous is False
        self.order.append("commit")
        return None


def test_offset_commit_happens_only_after_durable_handler_returns() -> None:
    order: list[str] = []
    consumer = Consumer(order)

    def handler(body: dict[str, Any], key: str | None) -> None:
        assert body["event_type"] == "products.changed"
        assert key == "product-1"
        order.append("durable-write")

    apply_then_commit(consumer, Message(), handler)

    assert order == ["durable-write", "commit"]


def test_handler_failure_never_commits_offset() -> None:
    order: list[str] = []
    consumer = Consumer(order)

    def handler(body: dict[str, Any], key: str | None) -> None:
        del body, key
        raise RuntimeError("index unavailable")

    try:
        apply_then_commit(consumer, Message(), handler)
    except RuntimeError:
        pass

    assert order == []
