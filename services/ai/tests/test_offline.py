"""Acceptance criterion 1: with `AI_BACKEND=stub`, nothing opens a connection.

Nine other services in this system call this one, and none of their CI runners
has network access. So this test does not merely check that the endpoints
work — it makes every outbound connection raise first, and then checks that
they still work.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Any attempt to reach the outside world becomes a loud failure."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the stub backend must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    yield


def test_every_endpoint_answers_with_the_network_unplugged(
    no_network: None, make_client: Callable[..., TestClient]
) -> None:
    client = make_client()

    health = client.get("/health")
    extract = client.post(
        "/v1/extract",
        json={"text": "لنت ترمز جلو پراید اصلی سایپا یدک", "schema_name": "offer_fields"},
    )
    judge = client.post(
        "/v1/judge",
        json={"a": "لنت ترمز جلو پراید اصلی سایپا یدک", "b": "لنت ترمز جلو پراید بوش"},
    )
    embed = client.post("/v1/embed", json={"texts": ["لنت ترمز جلو پراید"]})
    metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.json()["backend"] == "stub"
    assert extract.status_code == 200
    assert extract.json()["fields"]["brand"] == "سایپا یدک"
    assert judge.status_code == 200
    assert judge.json()["is_same"] is False
    assert embed.status_code == 200
    assert len(embed.json()["vectors"][0]) == 384
    assert metrics.status_code == 200


def test_the_openapi_document_is_served_offline(
    no_network: None, make_client: Callable[..., TestClient]
) -> None:
    client = make_client()
    assert client.get("/openapi.json").status_code == 200


def test_a_clone_with_no_environment_at_all_boots(
    no_network: None, monkeypatch: pytest.MonkeyPatch, make_client: Callable[..., TestClient]
) -> None:
    for name in ("AI_BACKEND", "AI_BASE_URL", "AI_API_KEY", "REDIS_URL", "AI_EMBED_BACKEND"):
        monkeypatch.delenv(name, raising=False)

    client = make_client()

    assert client.get("/health").json()["status"] == "ok"
