from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from billing.models import ClickEvent, CpcRate, Seller, WalletTransaction
from billing.reporting import reconcile_day


def auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-internal-api-token"}


def create_seller() -> Seller:
    return Seller.objects.create(
        seller_key="yadakyar",
        name="Yadakyar",
        domain="seller.example",
        is_panel=True,
        wallet_balance_toman=10_000,
    )


def create_click(*, occurred_at: datetime, cost_toman: int, suspicious: bool = False) -> ClickEvent:
    return ClickEvent.objects.create(
        product_uid=uuid.uuid4(),
        offer_uid=uuid.uuid4().hex,
        seller_key="yadakyar",
        price_toman=100_000,
        is_panel_offer=True,
        cost_toman=cost_toman,
        is_suspicious=suspicious,
        fraud_reasons=["known_bot"] if suspicious else [],
        ip_hash="a" * 64,
        user_agent_hash="b" * 64,
        fingerprint_hash="c" * 64,
        occurred_at=occurred_at,
    )


@pytest.mark.django_db
def test_stats_endpoint_returns_seeded_click_and_spend_figures() -> None:
    create_seller()
    now = datetime.now(tz=UTC)
    create_click(occurred_at=now - timedelta(days=1), cost_toman=900)
    create_click(occurred_at=now, cost_toman=4_200)
    create_click(occurred_at=now, cost_toman=0, suspicious=True)

    response = Client().get("/v1/sellers/yadakyar/stats", headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["total_clicks"] == 3
    assert body["suspicious_clicks"] == 1
    assert body["charged_clicks"] == 2
    assert body["spend_toman"] == 5_100
    assert sum(row["clicks"] for row in body["daily"]) == 3
    assert "impressions" not in body


@pytest.mark.django_db
def test_internal_api_requires_bearer_authentication() -> None:
    create_seller()
    assert Client().get("/v1/sellers/yadakyar/stats").status_code == 401


@pytest.mark.django_db
def test_wallet_topup_endpoint_is_idempotent() -> None:
    create_seller()
    client = Client()
    payload = {"amount_toman": 5_000, "gateway_reference": "gateway-ref-1"}
    first = client.post(
        "/v1/sellers/yadakyar/topup",
        data=payload,
        content_type="application/json",
        headers=auth(),
    )
    second = client.post(
        "/v1/sellers/yadakyar/topup",
        data=payload,
        content_type="application/json",
        headers=auth(),
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert Seller.objects.get().wallet_balance_toman == 15_000
    assert WalletTransaction.objects.filter(kind=WalletTransaction.Kind.TOPUP).count() == 1

    wallet = client.get("/v1/sellers/yadakyar/wallet", headers=auth())
    assert wallet.status_code == 200
    assert wallet.json()["balance_toman"] == 15_000
    assert len(wallet.json()["transactions"]) == 1


@pytest.mark.django_db
def test_rates_endpoint_returns_active_rate_card() -> None:
    create_seller()
    CpcRate.objects.create(
        name="low",
        min_price_toman=0,
        max_price_toman=100_000,
        cost_toman=900,
        effective_from=timezone.now(),
    )
    response = Client().get("/v1/rates", headers=auth())
    assert response.status_code == 200
    assert response.json()[0]["cost_toman"] == 900


@pytest.mark.django_db
def test_nightly_reconciliation_has_zero_discrepancies_for_seeded_day() -> None:
    account = create_seller()
    occurred_at = datetime.now(tz=UTC)
    click = create_click(occurred_at=occurred_at, cost_toman=900)
    WalletTransaction.objects.create(
        seller=account,
        kind=WalletTransaction.Kind.CHARGE,
        amount_toman=-900,
        balance_after_toman=9_100,
        click=click,
        occurred_at=occurred_at,
    )
    assert reconcile_day(date.today()) == []
