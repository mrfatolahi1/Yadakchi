"""`/health` and `/metrics` — what an operator and Prometheus see."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from ai.config import EMBEDDING_DIM
from ai.prompts import PROMPT_VERSION


def test_health_reports_backend_model_reachability_and_budget(client: TestClient) -> None:
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["backend"] == "stub"
    assert body["model"] == "stub-1"
    assert body["reachable"] is True
    assert body["embed_backend"] == "stub"
    assert body["dim"] == EMBEDDING_DIM
    assert body["prompt_version"] == PROMPT_VERSION
    assert body["schemas"] == ["offer_fields"]
    assert body["budget"]["day"]
    assert body["budget"]["limit"] == 3600.0
    assert body["budget"]["unit"] == "seconds"
    assert body["budget"]["exhausted"] is False
    assert body["cache"]["redis"] == "up"


def test_health_reports_budget_usage_after_a_call(client: TestClient) -> None:
    before = client.get("/health").json()["budget"]["used"]
    client.post("/v1/extract", json={"text": "فیلتر روغن پراید", "schema_name": "offer_fields"})
    after = client.get("/health").json()["budget"]["used"]

    assert after > before


def test_health_is_degraded_when_the_budget_is_spent(
    make_client: Callable[..., TestClient],
) -> None:
    spent = make_client(ai_daily_budget=1e-9)
    spent.post("/v1/extract", json={"text": "لنت ترمز پراید", "schema_name": "offer_fields"})

    body = spent.get("/health").json()
    assert body["status"] == "degraded"
    assert body["budget"]["exhausted"] is True


def test_health_says_disabled_when_no_redis_is_configured(
    make_client: Callable[..., TestClient],
) -> None:
    body = make_client().get("/health").json()
    assert body["cache"]["redis"] == "disabled"


def test_metrics_exposes_the_documented_names(client: TestClient) -> None:
    client.post("/v1/extract", json={"text": "فیلتر روغن پراید", "schema_name": "offer_fields"})
    body = client.get("/metrics").text

    for name in (
        "yadakchi_ai_calls_total",
        "yadakchi_ai_duration_seconds",
        "yadakchi_ai_cache_hits_total",
        "yadakchi_ai_tokens_total",
        "yadakchi_ai_budget_used_ratio",
    ):
        assert name in body
