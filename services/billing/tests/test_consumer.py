from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from billing.consumer import process_seller_event
from billing.events import SELLER_BILLING_CHANGED_TOPIC
from billing.management.commands import consume_sellers
from billing.models import OutboxEvent, ProcessedEvent, Seller


def utc_string(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def seller_event(
    *,
    event_id: uuid.UUID | None = None,
    updated_at: datetime | None = None,
    trust_score: float = 0.8,
    payload: dict[str, object] | None | object = ...,
) -> dict[str, object]:
    occurred = updated_at or datetime.now(tz=UTC)
    if payload is ...:
        actual_payload: dict[str, object] | None = {
            "seller_key": "yadakyar",
            "name": "یدکی‌یار",
            "domain": "yadakyar.example",
            "source_key": "yadakyar",
            "is_panel": True,
            "tier": "standard",
            "trust_score": trust_score,
            "price_accuracy": 0.9,
            "stock_accuracy": 0.85,
            "updated_at": utc_string(occurred),
        }
    else:
        actual_payload = payload  # type: ignore[assignment]
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "sellers.changed",
        "version": 1,
        "occurred_at": utc_string(occurred),
        "producer": "catalog",
        "trace_id": uuid.uuid4().hex,
        "payload": actual_payload,
    }


@pytest.mark.django_db
def test_duplicate_seller_delivery_is_idempotent_and_preserves_wallet() -> None:
    body = seller_event()
    assert process_seller_event(body, message_key="yadakyar") is True
    Seller.objects.filter(seller_key="yadakyar").update(wallet_balance_toman=12_345)

    assert process_seller_event(body, message_key="yadakyar") is False
    account = Seller.objects.get(seller_key="yadakyar")
    assert account.wallet_balance_toman == 12_345
    assert ProcessedEvent.objects.count() == 1


@pytest.mark.django_db
def test_out_of_order_seller_event_does_not_overwrite_newer_state() -> None:
    newer_time = datetime.now(tz=UTC)
    older_time = newer_time - timedelta(hours=1)
    process_seller_event(
        seller_event(updated_at=newer_time, trust_score=0.95), message_key="yadakyar"
    )
    process_seller_event(
        seller_event(updated_at=older_time, trust_score=0.2), message_key="yadakyar"
    )

    assert float(Seller.objects.get().trust_score) == pytest.approx(0.95)
    assert ProcessedEvent.objects.count() == 2


@pytest.mark.django_db
def test_seller_tombstone_preserves_financial_history() -> None:
    process_seller_event(seller_event(), message_key="yadakyar")
    Seller.objects.filter(seller_key="yadakyar").update(wallet_balance_toman=500)
    tombstone = seller_event(payload=None)

    process_seller_event(tombstone, message_key="yadakyar")

    account = Seller.objects.get()
    assert account.is_deleted is True
    assert account.wallet_balance_toman == 500
    outbox = OutboxEvent.objects.get(topic=SELLER_BILLING_CHANGED_TOPIC)
    assert outbox.body["payload"] is None


@pytest.mark.django_db
def test_stale_tombstone_does_not_delete_newer_seller_state() -> None:
    now = datetime.now(tz=UTC)
    process_seller_event(seller_event(updated_at=now), message_key="yadakyar")
    stale_tombstone = seller_event(updated_at=now - timedelta(hours=1), payload=None)

    process_seller_event(stale_tombstone, message_key="yadakyar")

    account = Seller.objects.get()
    assert account.is_deleted is False
    assert account.is_panel is True


def test_consumer_commits_only_after_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    class FakeMessage:
        def key(self) -> bytes:
            return b"yadakyar"

        def value(self) -> bytes:
            return b"{}"

    class FakeConsumer:
        def commit(self, *, message: Any, asynchronous: bool) -> None:
            del message, asynchronous
            order.append("commit")

    def durable_write(body: dict[str, object], *, message_key: str) -> bool:
        del body, message_key
        order.append("durable_write")
        return True

    monkeypatch.setattr(consume_sellers, "process_seller_event", durable_write)
    consume_sellers.Command()._process_message(FakeConsumer(), FakeMessage())  # type: ignore[arg-type]
    assert order == ["durable_write", "commit"]


def test_consumer_does_not_commit_when_durable_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed = False

    class FakeMessage:
        def key(self) -> bytes:
            return b"yadakyar"

        def value(self) -> bytes:
            return b"{}"

    class FakeConsumer:
        def commit(self, *, message: Any, asynchronous: bool) -> None:
            nonlocal committed
            del message, asynchronous
            committed = True

    def failed_write(body: dict[str, object], *, message_key: str) -> bool:
        del body, message_key
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(consume_sellers, "process_seller_event", failed_write)
    with pytest.raises(RuntimeError):
        consume_sellers.Command()._process_message(FakeConsumer(), FakeMessage())  # type: ignore[arg-type]
    assert committed is False
