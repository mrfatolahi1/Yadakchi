"""The budget guard — 429 `budget_exhausted`, never a silent degradation.

`enricher` is built to catch that exact status and code and fall back to its
rules-only cascade. If this service quietly returned emptier answers instead,
that fallback would never fire and a day's offers would be silently poorer.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from ai.budget import BudgetGuard, today
from ai.cache import Cache
from ai.config import Settings
from ai.errors import BudgetExhaustedError

EXTRACT = {"text": "لنت ترمز جلو پراید اصلی", "schema_name": "offer_fields"}


def test_an_exhausted_budget_returns_429_with_the_documented_code(
    make_client: Callable[..., TestClient], read_metric: Callable[..., float]
) -> None:
    """Acceptance criterion 5."""
    client = make_client(ai_daily_budget=1e-9)

    assert client.post("/v1/extract", json=EXTRACT).status_code == 200

    refused = client.post("/v1/extract", json={**EXTRACT, "text": "فیلتر روغن پژو 206"})

    assert refused.status_code == 429
    assert refused.json()["code"] == "budget_exhausted"
    assert refused.headers["Retry-After"].isdigit()
    assert read_metric("yadakchi_ai_budget_used_ratio") == 1.0


def test_judge_is_refused_too(make_client: Callable[..., TestClient]) -> None:
    client = make_client(ai_daily_budget=1e-9)
    client.post("/v1/extract", json=EXTRACT)

    refused = client.post("/v1/judge", json={"a": "لنت پراید", "b": "لنت تیبا"})

    assert refused.status_code == 429
    assert refused.json()["code"] == "budget_exhausted"


def test_a_cached_answer_is_still_served_when_the_budget_is_spent(
    make_client: Callable[..., TestClient], redis_client: Redis
) -> None:
    """An answer we already hold costs nothing; refusing it would be pure loss."""
    client = make_client(redis=redis_client, ai_daily_budget=1e-9)
    first = client.post("/v1/extract", json=EXTRACT)
    assert first.status_code == 200

    again = client.post("/v1/extract", json=EXTRACT)

    assert again.status_code == 200
    assert again.json()["cached"] is True


async def test_the_ratio_metric_tracks_partial_usage(
    make_settings: Callable[..., Settings], read_metric: Callable[..., float]
) -> None:
    settings = make_settings(ai_daily_budget=10.0)
    guard = BudgetGuard(settings, Cache(settings))

    await guard.record(2.5)

    assert read_metric("yadakchi_ai_budget_used_ratio") == pytest.approx(0.25)


async def test_check_raises_once_the_limit_is_reached(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(ai_daily_budget=1.0)
    guard = BudgetGuard(settings, Cache(settings))

    await guard.check()
    await guard.record(1.0)

    with pytest.raises(BudgetExhaustedError) as raised:
        await guard.check()

    assert raised.value.code == "budget_exhausted"
    assert raised.value.status_code == 429
    assert raised.value.detail is not None
    assert raised.value.detail["unit"] == "seconds"


async def test_a_disabled_budget_never_refuses(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(ai_budget_enabled=False, ai_daily_budget=1.0)
    guard = BudgetGuard(settings, Cache(settings))
    await guard.record(1000.0)

    await guard.check()  # must not raise


async def test_usage_is_shared_through_redis(
    make_settings: Callable[..., Settings], redis_client: Redis
) -> None:
    """Two workers, one counter — otherwise the limit is per process."""
    settings = make_settings(ai_daily_budget=10.0)
    shared = Cache(settings, redis=redis_client)
    await shared.connect()

    first = BudgetGuard(settings, shared)
    second = BudgetGuard(settings, shared)

    await first.record(4.0)
    await second.record(4.0)

    assert await second.used() == pytest.approx(8.0)
    assert await redis_client.ttl(f"ai:budget:{today()}") > 0


async def test_a_metered_provider_is_billed_per_token(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(ai_cost_per_1k_input=2.0, ai_cost_per_1k_output=6.0)
    guard = BudgetGuard(settings, Cache(settings))

    assert settings.budget_unit == "currency"
    cost = guard.cost_of(prompt_tokens=1000, completion_tokens=500, seconds=99.0)
    assert cost == pytest.approx(2.0 + 3.0)


async def test_a_local_model_is_billed_in_wall_clock_seconds(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    guard = BudgetGuard(settings, Cache(settings))

    assert settings.budget_unit == "seconds"
    assert guard.cost_of(prompt_tokens=9999, completion_tokens=9999, seconds=1.5) == 1.5


async def test_eighty_percent_logs_a_warning_once(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    settings = make_settings(ai_daily_budget=10.0)
    guard = BudgetGuard(settings, Cache(settings))

    with caplog.at_level("WARNING"):
        await guard.record(8.5)
        await guard.record(0.1)

    warnings = [record for record in caplog.records if "budget" in record.getMessage()]
    assert len(warnings) == 1
