from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import fakeredis
import pytest
from django.conf import LazySettings

from billing import click_queue
from billing.tokens import ClickIntent, sign_click_intent


@pytest.fixture
def redis_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(click_queue, "get_redis", lambda: client)
    return client


@pytest.fixture(autouse=True)
def billing_settings(settings: LazySettings) -> None:
    settings.CLICK_SIGNING_KEY = "test-click-signing-key"
    settings.PRIVACY_HASH_KEY = "test-privacy-hash-key"
    settings.INTERNAL_API_TOKEN = "test-internal-api-token"
    settings.PUBLIC_REFERER_HOSTS = ("yadakchi.ir", "www.yadakchi.ir")
    settings.CLICK_TOKEN_TTL_SECONDS = 1800
    settings.CLICK_TOKEN_FUTURE_SKEW_SECONDS = 60
    settings.IP_RATE_LIMIT = 10
    settings.IP_RATE_WINDOW_SECONDS = 300
    settings.FINGERPRINT_REPEAT_SECONDS = 1800
    settings.SELLER_VELOCITY_LIMIT = 200
    settings.SELLER_VELOCITY_WINDOW_SECONDS = 300


@pytest.fixture
def now() -> int:
    return int(time.time())


@pytest.fixture
def token_factory(now: int) -> Callable[..., str]:
    def factory(
        *,
        nonce: str | None = None,
        issued_at: int | None = None,
        destination_url: str = "https://seller.example/parts/123",
        price_toman: int | None = 120_000,
        is_panel_offer: bool = True,
        seller_key: str = "yadakyar",
        offer_uid: str = "ad1e2af57f36691329247db654602a4e",
        product_uid: uuid.UUID | None = None,
    ) -> str:
        intent = ClickIntent(
            product_uid=product_uid or uuid.uuid4(),
            offer_uid=offer_uid,
            seller_key=seller_key,
            destination_url=destination_url,
            price_toman=price_toman,
            is_panel_offer=is_panel_offer,
            issued_at=now if issued_at is None else issued_at,
            nonce=nonce or uuid.uuid4().hex,
        )
        return sign_click_intent(intent)

    return factory
