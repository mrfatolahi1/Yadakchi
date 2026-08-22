from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

import pytest
from django.db import connection
from django.test import Client

from billing.click_queue import PENDING_QUEUE, parse_queued_click
from billing.drain import drain_clicks
from billing.models import ClickEvent, CpcRate, Seller
from billing.redirect_view import RedirectResult


class NullPublisher:
    def publish(self, *, topic: str, key: str, body: dict[str, object]) -> None:
        del topic, key, body


def get_redirect(client: Client, path: str) -> Any:
    return client.get(
        path,
        headers={
            "user-agent": "Mozilla/5.0 Human Browser",
            "referer": "https://www.yadakchi.ir/products/brake-pad",
        },
        REMOTE_ADDR="203.0.113.42",
    )


@pytest.mark.django_db
def test_redirect_under_50ms_with_postgres_deliberately_stalled(
    redis_client: Any, token_factory: Any
) -> None:
    client = Client()
    warmup = token_factory(nonce="warmup-nonce-000000000000")
    assert get_redirect(client, f"/go/{warmup}").status_code == 302
    redis_client.flushall()

    database_calls = 0

    def stalled_database(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        nonlocal database_calls
        del sql, params, many, context
        database_calls += 1
        time.sleep(0.1)
        return execute()

    token = token_factory(nonce="timed-nonce-0000000000000")
    started = time.perf_counter()
    with connection.execute_wrapper(stalled_database):
        response = get_redirect(client, f"/go/{token}")
    elapsed = time.perf_counter() - started

    assert response.status_code == 302
    assert response["Location"] == "https://seller.example/parts/123"
    assert elapsed < 0.05
    assert database_calls == 0
    assert redis_client.llen(PENDING_QUEUE) == 1


@pytest.mark.django_db
def test_tampered_expired_and_replayed_tokens_are_rejected_without_queueing(
    redis_client: Any, token_factory: Any, now: int
) -> None:
    client = Client()
    valid = token_factory(nonce="replay-nonce-000000000000")
    tampered = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    expired = token_factory(nonce="expired-nonce-00000000000", issued_at=now - 1801)

    assert get_redirect(client, f"/go/{tampered}").status_code == 400
    assert get_redirect(client, f"/go/{expired}").status_code == 400
    assert redis_client.llen(PENDING_QUEUE) == 0

    assert get_redirect(client, f"/go/{valid}").status_code == 302
    assert get_redirect(client, f"/go/{valid}").status_code == 400
    assert redis_client.llen(PENDING_QUEUE) == 1
    assert ClickEvent.objects.count() == 0


@pytest.mark.django_db
def test_destination_query_parameter_is_ignored(redis_client: Any, token_factory: Any) -> None:
    token = token_factory(destination_url="https://seller.example/safe")
    response = get_redirect(Client(), f"/go/{token}?destination_url=https://evil.example/phish")

    assert response.status_code == 302
    assert response["Location"] == "https://seller.example/safe"
    queued = parse_queued_click(redis_client.lindex(PENDING_QUEUE, 0))
    assert not hasattr(queued, "destination_url")


@pytest.mark.django_db
def test_raw_ip_and_user_agent_are_never_persisted(redis_client: Any, token_factory: Any) -> None:
    Seller.objects.create(
        seller_key="yadakyar",
        name="Yadakyar",
        domain="seller.example",
        is_panel=True,
        wallet_balance_toman=10_000,
    )
    CpcRate.objects.create(
        name="low",
        min_price_toman=0,
        max_price_toman=None,
        cost_toman=900,
        effective_from="2020-01-01T00:00:00Z",
    )
    raw_ip = "198.51.100.77"
    raw_user_agent = "Sensitive Browser Fingerprint/123"
    token = token_factory()
    response = Client().get(
        f"/go/{token}",
        headers={
            "user-agent": raw_user_agent,
            "referer": "https://yadakchi.ir/product/1",
        },
        REMOTE_ADDR=raw_ip,
    )
    assert response.status_code == 302

    queued_raw = redis_client.lindex(PENDING_QUEUE, 0)
    assert raw_ip not in queued_raw
    assert raw_user_agent not in queued_raw
    queued = parse_queued_click(queued_raw)
    assert len(queued.ip_hash) == 64
    assert len(queued.user_agent_hash) == 64

    drain_clicks(redis_client=redis_client, publisher=NullPublisher())
    click = ClickEvent.objects.get()
    assert click.ip_hash != raw_ip
    assert click.user_agent_hash != raw_user_agent


def test_production_asgi_redirect_bypasses_django_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from billing import asgi

    async def forbidden_django(_scope: Any, _receive: Any, _send: Any) -> None:
        raise AssertionError("Django middleware stack was invoked")

    def successful_redirect(
        token: str, *, headers: dict[str, str], remote_addr: str, now: int | None = None
    ) -> RedirectResult:
        del token, headers, remote_addr, now
        return RedirectResult(302, "https://seller.example/parts/1")

    monkeypatch.setattr(asgi, "django_application", forbidden_django)
    monkeypatch.setattr(asgi, "process_redirect", successful_redirect)
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Mapping[str, Any]) -> None:
        messages.append(dict(message))

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/go/signed-token",
        "headers": [],
        "client": ("203.0.113.10", 1234),
    }
    asyncio.run(asgi.application(scope, receive, send))
    assert messages[0]["status"] == 302
    assert (b"location", b"https://seller.example/parts/1") in messages[0]["headers"]
