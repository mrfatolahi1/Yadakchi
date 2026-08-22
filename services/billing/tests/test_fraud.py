from __future__ import annotations

from typing import Any

import pytest
from django.conf import LazySettings
from django.test import Client

from billing.click_queue import PENDING_QUEUE, parse_queued_click
from billing.drain import drain_clicks
from billing.events import REVIEW_REQUESTED_TOPIC
from billing.models import ClickEvent, CpcRate, OutboxEvent, Seller, WalletTransaction


class NullPublisher:
    def publish(self, *, topic: str, key: str, body: dict[str, object]) -> None:
        del topic, key, body


def get_redirect(
    client: Client,
    path: str,
    *,
    user_agent: str = "Mozilla/5.0",
    ip: str = "203.0.113.5",
    referer: str | None = "https://yadakchi.ir/products/1",
) -> Any:
    request_headers = {"user-agent": user_agent}
    if referer is not None:
        request_headers["referer"] = referer
    return client.get(path, headers=request_headers, REMOTE_ADDR=ip)


@pytest.fixture
def billable_seller() -> Seller:
    account = Seller.objects.create(
        seller_key="yadakyar",
        name="Yadakyar",
        domain="seller.example",
        is_panel=True,
        wallet_balance_toman=20_000,
    )
    CpcRate.objects.create(
        name="all",
        min_price_toman=0,
        cost_toman=900,
        effective_from="2020-01-01T00:00:00Z",
    )
    return account


@pytest.mark.django_db
def test_per_ip_limit_marks_suspicious_skips_charge_and_still_redirects(
    redis_client: Any,
    token_factory: Any,
    billable_seller: Seller,
    settings: LazySettings,
) -> None:
    del billable_seller
    settings.IP_RATE_LIMIT = 1
    client = Client()
    first = token_factory(offer_uid="ad1e2af57f36691329247db654602a4e")
    second = token_factory(offer_uid="dece8380a022dfec9a040ea3221aedab")

    assert get_redirect(client, f"/go/{first}").status_code == 302
    assert get_redirect(client, f"/go/{second}").status_code == 302
    drain_clicks(limit=2, redis_client=redis_client, publisher=NullPublisher())

    clicks = list(ClickEvent.objects.order_by("occurred_at", "click_id"))
    assert sorted(click.cost_toman for click in clicks) == [0, 900]
    suspicious = next(click for click in clicks if click.is_suspicious)
    assert "ip_rate_limit" in suspicious.fraud_reasons
    assert WalletTransaction.objects.count() == 1


@pytest.mark.django_db
def test_known_bot_is_redirected_recorded_and_never_charged(
    redis_client: Any, token_factory: Any, billable_seller: Seller
) -> None:
    del billable_seller
    token = token_factory()
    response = get_redirect(Client(), f"/go/{token}", user_agent="Googlebot/2.1")
    assert response.status_code == 302

    queued = parse_queued_click(redis_client.lindex(PENDING_QUEUE, 0))
    assert queued.is_suspicious is True
    assert "known_bot" in queued.fraud_reasons
    drain_clicks(redis_client=redis_client, publisher=NullPublisher())
    assert ClickEvent.objects.get().cost_toman == 0
    assert WalletTransaction.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("referer", "reason"),
    [(None, "missing_referer"), ("https://evil.example/page", "foreign_referer")],
)
def test_missing_or_foreign_referer_is_flagged(
    redis_client: Any,
    token_factory: Any,
    referer: str | None,
    reason: str,
) -> None:
    response = get_redirect(
        Client(),
        f"/go/{token_factory()}",
        ip="203.0.113.8",
        referer=referer,
    )
    assert response.status_code == 302
    queued = parse_queued_click(redis_client.lindex(PENDING_QUEUE, 0))
    assert reason in queued.fraud_reasons


@pytest.mark.django_db
def test_velocity_anomaly_creates_one_review_request(
    redis_client: Any,
    token_factory: Any,
    billable_seller: Seller,
    settings: LazySettings,
) -> None:
    del billable_seller
    settings.SELLER_VELOCITY_LIMIT = 1
    first = token_factory(offer_uid="ad1e2af57f36691329247db654602a4e")
    second = token_factory(offer_uid="dece8380a022dfec9a040ea3221aedab")
    client = Client()
    assert get_redirect(client, f"/go/{first}", ip="203.0.113.1").status_code == 302
    assert get_redirect(client, f"/go/{second}", ip="203.0.113.2").status_code == 302
    drain_clicks(limit=2, redis_client=redis_client, publisher=NullPublisher())

    reviews = OutboxEvent.objects.filter(topic=REVIEW_REQUESTED_TOPIC)
    assert reviews.count() == 1
    assert reviews.get().body["payload"]["kind"] == "seller_click_velocity"
