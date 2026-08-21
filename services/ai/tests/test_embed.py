"""`POST /v1/embed` — exactly 384 dimensions, for `matcher` and `search`."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.api_models import EmbedResponse
from ai.config import EMBEDDING_DIM, MAX_EMBED_TEXTS


def _embed(client: TestClient, texts: list[str]) -> dict[str, Any]:
    response = client.post("/v1/embed", json={"texts": texts})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_the_contract_constant_and_the_response_literal_agree() -> None:
    """`dim` is written out as a literal in the schema; keep the two in step."""
    assert EMBEDDING_DIM == 384
    assert EmbedResponse.model_fields["dim"].default == EMBEDDING_DIM


def test_vectors_are_exactly_384_dimensions(client: TestClient) -> None:
    body = _embed(client, ["لنت ترمز جلو پراید", "فیلتر روغن پژو 206"])

    assert body["dim"] == 384
    assert len(body["vectors"]) == 2
    for vector in body["vectors"]:
        assert len(vector) == 384


def test_vectors_are_unit_length(client: TestClient) -> None:
    vector = _embed(client, ["کمک فنر جلو پژو 405"])["vectors"][0]
    norm = math.sqrt(sum(value * value for value in vector))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_embedding_is_deterministic(client: TestClient) -> None:
    first = _embed(client, ["تسمه تایم پژو 206"])["vectors"][0]
    second = _embed(client, ["تسمه تایم پژو 206"])["vectors"][0]
    assert first == second


def test_similar_titles_are_closer_than_unrelated_ones(client: TestClient) -> None:
    vectors = _embed(
        client,
        [
            "لنت ترمز جلو پراید اصلی",
            "لنت ترمز جلو پراید سایپا",
            "رادیاتور آب سمند",
        ],
    )["vectors"]

    def dot(left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right, strict=True))

    assert dot(vectors[0], vectors[1]) > dot(vectors[0], vectors[2])


def test_order_is_preserved_and_duplicates_are_answered_once(client: TestClient) -> None:
    texts = ["الف", "ب", "الف", "ج", "ب"]
    vectors = _embed(client, texts)["vectors"]

    assert len(vectors) == len(texts)
    assert vectors[0] == vectors[2]
    assert vectors[1] == vectors[4]
    assert vectors[0] != vectors[1]


def test_a_repeated_text_is_served_from_cache(
    client: TestClient, read_metric: Callable[..., float]
) -> None:
    before = read_metric("yadakchi_ai_cache_hits_total", op="embed")
    _embed(client, ["واشر سرسیلندر پراید"])
    _embed(client, ["واشر سرسیلندر پراید"])
    after = read_metric("yadakchi_ai_cache_hits_total", op="embed")

    assert after - before == 1


def test_the_maximum_batch_is_accepted(client: TestClient) -> None:
    texts = [f"قطعه شماره {index}" for index in range(MAX_EMBED_TEXTS)]
    body = _embed(client, texts)
    assert len(body["vectors"]) == MAX_EMBED_TEXTS


def test_more_than_the_maximum_is_refused(client: TestClient) -> None:
    texts = [f"قطعه شماره {index}" for index in range(MAX_EMBED_TEXTS + 1)]
    response = client.post("/v1/embed", json={"texts": texts})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_an_empty_batch_is_refused(client: TestClient) -> None:
    assert client.post("/v1/embed", json={"texts": []}).status_code == 422


def test_batches_are_chunked_at_the_configured_size(
    make_client: Callable[..., TestClient], read_metric: Callable[..., float]
) -> None:
    """Internal batching: 70 new texts in chunks of 32 is three encode calls."""
    client = make_client(ai_embed_batch_size=32)
    before = read_metric("yadakchi_ai_model_invocations_total", op="embed", backend="stub")
    _embed(client, [f"عنوان یکتا {index}" for index in range(70)])
    after = read_metric("yadakchi_ai_model_invocations_total", op="embed", backend="stub")

    assert after - before == 3


def test_the_model_name_is_reported(client: TestClient) -> None:
    assert _embed(client, ["پراید"])["model"] == "stub-hashed-384-v1"
