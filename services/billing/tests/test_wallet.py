from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from billing.click_queue import PENDING_QUEUE, QueuedClick
from billing.drain import drain_clicks
from billing.events import CLICKS_RECORDED_TOPIC, SELLER_BILLING_CHANGED_TOPIC
from billing.models import ClickEvent, CpcRate, OutboxEvent, Seller, WalletTransaction
from billing.wallet import adjust_wallet, process_queued_click, record_topup

ROOT = Path(__file__).resolve().parents[1]


class CapturingPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, object]]] = []

    def publish(self, *, topic: str, key: str, body: dict[str, object]) -> None:
        self.messages.append((topic, key, body))


def seller(*, balance: int = 10_000, active: bool = True) -> Seller:
    return Seller.objects.create(
        seller_key="yadakyar",
        name="Yadakyar",
        domain="seller.example",
        is_panel=True,
        wallet_balance_toman=balance,
        panel_offers_active=active,
    )


def rate(*, cost: int = 900, minimum: int = 0, maximum: int | None = None) -> CpcRate:
    return CpcRate.objects.create(
        name=f"band-{minimum}",
        min_price_toman=minimum,
        max_price_toman=maximum,
        cost_toman=cost,
        effective_from=timezone.now(),
    )


def queued_click(
    *,
    click_id: uuid.UUID | None = None,
    price_toman: int | None = 120_000,
    is_panel_offer: bool = True,
    is_suspicious: bool = False,
    offer_uid: str = "ad1e2af57f36691329247db654602a4e",
    occurred_at: datetime | None = None,
) -> QueuedClick:
    identity = click_id or uuid.uuid4()
    return QueuedClick(
        click_id=str(identity),
        product_uid=str(uuid.uuid4()),
        offer_uid=offer_uid,
        seller_key="yadakyar",
        price_toman=price_toman,
        is_panel_offer=is_panel_offer,
        occurred_at=(occurred_at or datetime.now(tz=UTC)).isoformat().replace("+00:00", "Z"),
        trace_id=str(identity),
        ip_hash="a" * 64,
        user_agent_hash="b" * 64,
        fingerprint_hash="c" * 64,
        is_suspicious=is_suspicious,
        fraud_reasons=["known_bot"] if is_suspicious else [],
    )


def validate_contract(filename: str, body: dict[str, Any]) -> None:
    schema = json.loads((ROOT / "contracts" / "published" / filename).read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(body)


@pytest.mark.django_db
def test_duplicate_drain_delivery_never_double_charges_or_emits_twice(
    redis_client: Any,
) -> None:
    seller(balance=10_000)
    rate(cost=900)
    queued = queued_click()
    raw = queued.model_dump_json()
    redis_client.lpush(PENDING_QUEUE, raw, raw)
    publisher = CapturingPublisher()

    assert drain_clicks(limit=2, redis_client=redis_client, publisher=publisher) == 2

    account = Seller.objects.get(seller_key="yadakyar")
    assert account.wallet_balance_toman == 9_100
    assert ClickEvent.objects.count() == 1
    assert WalletTransaction.objects.filter(kind=WalletTransaction.Kind.CHARGE).count() == 1
    click_messages = [
        message for message in publisher.messages if message[0] == CLICKS_RECORDED_TOPIC
    ]
    assert len(click_messages) == 1
    validate_contract("yadakchi.clicks.recorded.v1.json", click_messages[0][2])


@pytest.mark.django_db
def test_exact_depletion_suspends_panel_offers_but_crawled_click_stays_free() -> None:
    seller(balance=900)
    rate(cost=900)

    charged = process_queued_click(queued_click())
    account = Seller.objects.get(seller_key="yadakyar")
    assert charged.click.cost_toman == 900
    assert account.wallet_balance_toman == 0
    assert account.panel_offers_active is False

    state = OutboxEvent.objects.get(topic=SELLER_BILLING_CHANGED_TOPIC)
    validate_contract("yadakchi.seller_billing.changed.v1.json", state.body)
    assert state.body["payload"]["panel_offers_active"] is False
    assert state.body["payload"]["suspension_reason"] == "zero_balance"

    crawled = process_queued_click(
        queued_click(
            is_panel_offer=False,
            offer_uid="f6254affc0a4ab65d4ac7fa35e556eef",
        )
    )
    assert crawled.click.cost_toman == 0
    assert OutboxEvent.objects.filter(topic=SELLER_BILLING_CHANGED_TOPIC).count() == 1


@pytest.mark.django_db
def test_insufficient_balance_preserves_remainder_charges_zero_and_suspends() -> None:
    seller(balance=500)
    rate(cost=900)

    result = process_queued_click(queued_click())
    account = Seller.objects.get(seller_key="yadakyar")
    assert result.click.cost_toman == 0
    assert account.wallet_balance_toman == 500
    assert account.panel_offers_active is False
    assert WalletTransaction.objects.count() == 0
    state = OutboxEvent.objects.get(topic=SELLER_BILLING_CHANGED_TOPIC)
    assert state.body["payload"]["suspension_reason"] == "insufficient_balance"


@pytest.mark.django_db
def test_topup_is_idempotent_and_reactivates_panel_offers() -> None:
    seller(balance=500, active=False)

    first = record_topup(seller_key="yadakyar", amount_toman=2_000, reference="gateway-1")
    second = record_topup(seller_key="yadakyar", amount_toman=2_000, reference="gateway-1")

    account = Seller.objects.get(seller_key="yadakyar")
    assert first.transaction_id == second.transaction_id
    assert account.wallet_balance_toman == 2_500
    assert account.panel_offers_active is True
    assert WalletTransaction.objects.count() == 1
    state = OutboxEvent.objects.get(topic=SELLER_BILLING_CHANGED_TOPIC)
    assert state.body["payload"]["panel_offers_active"] is True
    assert state.body["payload"]["suspension_reason"] is None


@pytest.mark.django_db
def test_manual_adjustment_is_atomic_and_cannot_overdraw_prepaid_wallet() -> None:
    seller(balance=500)
    adjustment = adjust_wallet(seller_key="yadakyar", amount_toman=-200, reference="manual:test-1")
    assert adjustment.balance_after_toman == 300
    assert Seller.objects.get().wallet_balance_toman == 300

    with pytest.raises(ValueError, match="negative"):
        adjust_wallet(seller_key="yadakyar", amount_toman=-301, reference="manual:test-2")
    assert Seller.objects.get().wallet_balance_toman == 300


@pytest.mark.django_db
def test_rate_is_resolved_by_price_band_and_frozen_on_click() -> None:
    seller(balance=100_000)
    low = rate(cost=900, minimum=0, maximum=100_000)
    high = rate(cost=4_200, minimum=100_000)

    low_click = process_queued_click(
        queued_click(price_toman=None, offer_uid="dece8380a022dfec9a040ea3221aedab")
    ).click
    high_click = process_queued_click(queued_click(price_toman=3_000_000)).click
    assert low_click.cost_toman == 900
    assert high_click.cost_toman == 4_200

    low.cost_toman = 2_000
    low.save(update_fields=["cost_toman"])
    high.cost_toman = 8_000
    high.save(update_fields=["cost_toman"])
    low_click.refresh_from_db()
    high_click.refresh_from_db()
    assert low_click.cost_toman == 900
    assert high_click.cost_toman == 4_200


@pytest.mark.django_db
def test_delayed_drain_uses_rate_card_effective_at_click_time() -> None:
    seller(balance=100_000)
    now = datetime.now(tz=UTC)
    CpcRate.objects.create(
        name="old",
        min_price_toman=0,
        cost_toman=900,
        effective_from=now - timedelta(days=2),
        effective_to=now - timedelta(days=1),
    )
    CpcRate.objects.create(
        name="new",
        min_price_toman=0,
        cost_toman=4_200,
        effective_from=now - timedelta(days=1),
    )

    delayed = process_queued_click(queued_click(occurred_at=now - timedelta(hours=36))).click
    assert delayed.cost_toman == 900


@pytest.mark.django_db
def test_suspicious_panel_click_is_recorded_but_never_charged() -> None:
    seller(balance=10_000)
    rate(cost=900)

    result = process_queued_click(queued_click(is_suspicious=True))
    assert result.click.is_suspicious is True
    assert result.click.cost_toman == 0
    assert Seller.objects.get().wallet_balance_toman == 10_000
    assert WalletTransaction.objects.count() == 0
