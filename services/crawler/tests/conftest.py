from collections.abc import Mapping
from typing import Any

import pytest

from crawler.models import Source


class RecordingPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    def publish(self, topic: str, key: str, body: Mapping[str, Any]) -> None:
        self.messages.append((topic, key, dict(body)))


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def source(db: object) -> Source:
    del db
    return Source.objects.create(
        key="test-source",
        name="Test Source",
        base_url="https://seller.example",
        kind=Source.Kind.HTML,
        adapter_key="isacostore",
        priority=70,
        politeness_delay_ms=100,
    )
