"""Shared fixtures.

Every test in this suite runs with **no network access**: the default backend
is the stub, Redis is `fakeredis`, and the one test that exercises a real HTTP
provider does it through an `httpx` mock transport. That is not a convenience
— nine other services depend on this service's stub, and their CI has no
network either.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

# Set before anything imports the app: ai.main builds a module-level app.
os.environ.setdefault("AI_BACKEND", "stub")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from redis.asyncio import Redis

from ai.backends.base import ModelBackend
from ai.cache import Cache
from ai.config import Settings, get_settings
from ai.embeddings import EmbeddingProvider
from ai.main import create_app


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def redis_client() -> Redis:
    """An in-memory Redis. Real semantics — TTLs, INCRBYFLOAT — no server."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        defaults: dict[str, Any] = {
            "ai_backend": "stub",
            "redis_url": None,
            "ai_retry_backoff_seconds": 0.0,
            "ai_daily_budget": 3600.0,
        }
        return Settings(**{**defaults, **overrides})

    return factory


@pytest.fixture
def make_app(make_settings: Callable[..., Settings]) -> Callable[..., FastAPI]:
    def factory(
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        redis: Redis | None = None,
        backend: ModelBackend | None = None,
        embedder: EmbeddingProvider | None = None,
        **overrides: Any,
    ) -> FastAPI:
        return create_app(
            settings or make_settings(**overrides),
            transport=transport,
            redis=redis,
            backend=backend,
            embedder=embedder,
        )

    return factory


@pytest.fixture
def make_client(make_app: Callable[..., FastAPI]) -> Iterator[Callable[..., TestClient]]:
    """A started TestClient — the lifespan runs, so startup checks run too."""
    clients: list[TestClient] = []

    def factory(**kwargs: Any) -> TestClient:
        client = TestClient(make_app(**kwargs))
        client.__enter__()
        clients.append(client)
        return client

    yield factory

    for client in reversed(clients):
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient], redis_client: Redis) -> TestClient:
    """The ordinary case: stub backend, in-memory Redis, budget generous."""
    return make_client(redis=redis_client)


def metric(name: str, **labels: str) -> float:
    """One sample from the default registry, or 0.0 when it has none yet."""
    value = REGISTRY.get_sample_value(name, labels or None)
    return float(value) if value is not None else 0.0


@pytest.fixture
def read_metric() -> Callable[..., float]:
    return metric


@pytest.fixture
def cache(make_settings: Callable[..., Settings], redis_client: Redis) -> Cache:
    return Cache(make_settings(), redis=redis_client)
