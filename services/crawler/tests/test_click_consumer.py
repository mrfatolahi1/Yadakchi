import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from crawler.adapters.base import ListingStub
from crawler.archive import ArchiveService
from crawler.consumers.clicks import ClickConsumerRunner, consume_click_event
from crawler.events import ClickRecordedEvent
from crawler.models import ClickSignal, ConsumedClick, Source
from crawler.observations import observe_listing
from tests.test_archive import MemoryObjectStore


def click_body(offer_uid: str, click_id: str = "click-1") -> dict[str, Any]:
    occurred_at = datetime(2026, 8, 21, 9, tzinfo=UTC)
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "clicks.recorded",
        "version": 1,
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "producer": "billing",
        "trace_id": "trace-click",
        "payload": {
            "click_id": click_id,
            "product_uid": str(uuid.uuid4()),
            "offer_uid": offer_uid,
            "seller_key": "seller",
            "cost_toman": 1000,
            "is_suspicious": False,
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        },
    }


def create_observation(source: Source) -> str:
    observed_at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    archive = (
        ArchiveService(MemoryObjectStore())
        .archive(source, "https://seller.example/page", b"raw page", 200, observed_at)
        .document
    )
    result = observe_listing(
        source,
        archive,
        ListingStub(
            external_key="sku-clicked",
            url="https://seller.example/p/sku-clicked",
            raw_title="raw",
            raw_price_text=None,
            raw_stock_text=None,
            image_url=None,
            raw_fragment="<article>raw</article>",
        ),
        observed_at,
    )
    return result.observation.offer_uid


@pytest.mark.django_db
def test_click_consumer_duplicate_delivery_does_not_double_count(source: Source) -> None:
    uid = create_observation(source)
    event = ClickRecordedEvent.model_validate(click_body(uid))
    now = datetime(2026, 8, 21, 10, tzinfo=UTC)

    first = consume_click_event(event, now=now)
    duplicate = consume_click_event(event, now=now)

    assert first.created is True
    assert first.matched is True
    assert duplicate.created is False
    assert ConsumedClick.objects.count() == 1
    assert ClickSignal.objects.get(offer_uid=uid).count_7d == 1


class FakeMessage:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def error(self) -> None:
        return None

    def value(self) -> bytes:
        return json.dumps(self.body).encode()


class CommitCheckingConsumer:
    def __init__(self, message: FakeMessage, click_id: str) -> None:
        self.message = message
        self.click_id = click_id
        self.committed = False

    def poll(self, timeout: float) -> FakeMessage:
        del timeout
        return self.message

    def commit(self, message: FakeMessage, asynchronous: bool = False) -> None:
        del message, asynchronous
        assert ConsumedClick.objects.filter(click_id=self.click_id).exists()
        self.committed = True


@pytest.mark.django_db
def test_click_offset_commits_only_after_durable_receipt(source: Source) -> None:
    uid = create_observation(source)
    body = click_body(uid, click_id="click-durable")
    consumer = CommitCheckingConsumer(FakeMessage(body), "click-durable")

    created = ClickConsumerRunner(consumer).run_once()

    assert created is True
    assert consumer.committed is True
