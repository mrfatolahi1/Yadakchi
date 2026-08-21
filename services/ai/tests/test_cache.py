"""Caching — mandatory, and keyed exactly as the spec says.

Duplicate seller titles are the norm, so the property that matters is the one
in `test_identical_extract_calls_reach_the_model_once`: the second identical
request must not cost anything.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from ai.cache import Cache, cache_key, canonical
from ai.config import Settings

BASE = {
    "backend": "local",
    "model": "qwen2.5:7b-instruct",
    "prompt_version": "v1",
    "operation": "extract",
    "payload": "لنت ترمز جلو پراید",
}


def test_the_key_is_sha256_of_all_five_components() -> None:
    key = cache_key(**BASE)
    assert len(key) == 64
    assert int(key, 16) >= 0


@pytest.mark.parametrize(
    "component", ["backend", "model", "prompt_version", "operation", "payload"]
)
def test_changing_any_component_changes_the_key(component: str) -> None:
    changed = {**BASE, component: BASE[component] + "-different"}
    assert cache_key(**changed) != cache_key(**BASE)


def test_the_key_is_stable_across_calls() -> None:
    assert cache_key(**BASE) == cache_key(**BASE)


def test_canonical_ignores_key_order() -> None:
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})


async def test_values_survive_a_round_trip_through_redis(cache: Cache) -> None:
    await cache.connect()
    await cache.set("k", {"fields": {"brand": "ایساکو"}})

    assert await cache.get("k") == {"fields": {"brand": "ایساکو"}}
    assert cache.redis_status == "up"


async def test_a_redis_failure_degrades_to_the_lru_instead_of_failing(
    make_settings: Callable[..., Settings], redis_client: Redis
) -> None:
    cache = Cache(make_settings(), redis=redis_client)
    await cache.connect()
    await cache.set("k", {"value": 1})
    await redis_client.aclose()  # the server goes away mid-flight

    # The LRU still answers, and a miss on a broken Redis is a miss, not a 500.
    assert await cache.get("k") == {"value": 1}
    assert await cache.get("never-written") is None


async def test_the_lru_is_bounded(make_settings: Callable[..., Settings]) -> None:
    cache = Cache(make_settings(ai_cache_lru_size=2))
    for index in range(5):
        await cache.set(f"k{index}", {"index": index})

    assert cache.entries == 2
    assert await cache.get("k0") is None
    assert await cache.get("k4") == {"index": 4}


async def test_a_ttl_is_always_set_so_redis_can_evict(
    make_settings: Callable[..., Settings], redis_client: Redis
) -> None:
    """platform Redis runs `volatile-lru`: an entry with no TTL is never
    evictable and would eventually crowd out someone's lock."""
    cache = Cache(make_settings(ai_cache_ttl_seconds=1234), redis=redis_client)
    await cache.connect()
    await cache.set("k", {"value": 1})

    assert await redis_client.ttl("ai:cache:k") == 1234


def test_identical_extract_calls_reach_the_model_once(
    client: TestClient, read_metric: Callable[..., float]
) -> None:
    """Acceptance criterion 4, proven by the metrics."""
    body = {"text": "لنت ترمز جلو پراید اصلی سایپا یدک", "schema_name": "offer_fields"}
    invocations = ("yadakchi_ai_model_invocations_total", {"op": "extract", "backend": "stub"})
    hits = ("yadakchi_ai_cache_hits_total", {"op": "extract"})
    cached_calls = (
        "yadakchi_ai_calls_total",
        {"op": "extract", "backend": "stub", "status": "cached"},
    )

    before = [read_metric(name, **labels) for name, labels in (invocations, hits, cached_calls)]

    first = client.post("/v1/extract", json=body).json()
    second = client.post("/v1/extract", json=body).json()

    after = [read_metric(name, **labels) for name, labels in (invocations, hits, cached_calls)]

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["fields"] == first["fields"]
    assert after[0] - before[0] == 1, "the model must be invoked exactly once"
    assert after[1] - before[1] == 1, "and the second call must be a cache hit"
    assert after[2] - before[2] == 1


def test_the_hit_ratio_is_exposed(client: TestClient, read_metric: Callable[..., float]) -> None:
    body = {"text": "فیلتر روغن پژو 405", "schema_name": "offer_fields"}
    client.post("/v1/extract", json=body)
    client.post("/v1/extract", json=body)

    assert read_metric("yadakchi_ai_cache_hit_ratio", op="extract") > 0.0


def test_a_second_process_sharing_redis_gets_the_hit(
    make_client: Callable[..., TestClient], redis_client: Redis
) -> None:
    """The LRU is per process; Redis is what makes the cache shared."""
    body = {"text": "دسته موتور پژو 206 اصلی", "schema_name": "offer_fields"}
    first_worker = make_client(redis=redis_client)
    first_worker.post("/v1/extract", json=body)

    second_worker = make_client(redis=redis_client)
    answer: dict[str, Any] = second_worker.post("/v1/extract", json=body).json()

    assert answer["cached"] is True


def test_bumping_the_prompt_version_invalidates_the_cache(
    make_client: Callable[..., TestClient], redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"text": "سیبک فرمان پراید عظام", "schema_name": "offer_fields"}
    make_client(redis=redis_client).post("/v1/extract", json=body)

    monkeypatch.setattr("ai.service.PROMPT_VERSION", "v2")
    answer = make_client(redis=redis_client).post("/v1/extract", json=body).json()

    assert answer["cached"] is False
