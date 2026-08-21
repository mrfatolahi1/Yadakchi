"""`POST /v1/judge` — the call `matcher` makes when its ladder runs out.

The rules under test are the ones written into `prompts/judge_same_part.txt`,
in the same order of authority. Rule 1 is the one with teeth: different brands
are never the same part, whatever else the two titles share.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.titles import (
    DIFFERENT_BRAND_PAIRS,
    DIFFERENT_PRODUCT_PAIRS,
    SAME_PART_PAIRS,
    JudgePair,
)

from ai.api_models import contains_persian


def _judge(client: TestClient, a: str, b: str, **extra: Any) -> dict[str, Any]:
    response = client.post("/v1/judge", json={"a": a, "b": b, **extra})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.parametrize("pair", DIFFERENT_BRAND_PAIRS, ids=[p.note for p in DIFFERENT_BRAND_PAIRS])
def test_two_different_brands_are_never_the_same_part(client: TestClient, pair: JudgePair) -> None:
    """Acceptance criterion 8, over the whole different-brand fixture set."""
    body = _judge(client, pair.a, pair.b)

    assert body["is_same"] is False, f"{pair.a!r} vs {pair.b!r} -> {body!r}"
    assert body["confidence"] >= 0.9
    assert "برند" in body["reason_fa"]


@pytest.mark.parametrize("pair", SAME_PART_PAIRS, ids=[p.note for p in SAME_PART_PAIRS])
def test_the_same_part_written_two_ways_is_accepted(client: TestClient, pair: JudgePair) -> None:
    body = _judge(client, pair.a, pair.b)
    assert body["is_same"] is True, f"{pair.a!r} vs {pair.b!r} -> {body!r}"


@pytest.mark.parametrize(
    "pair", DIFFERENT_PRODUCT_PAIRS, ids=[p.note for p in DIFFERENT_PRODUCT_PAIRS]
)
def test_grade_and_pack_quantity_separate_products(client: TestClient, pair: JudgePair) -> None:
    body = _judge(client, pair.a, pair.b)
    assert body["is_same"] is False, f"{pair.a!r} vs {pair.b!r} -> {body!r}"


def test_reason_fa_is_mandatory_and_actually_persian(client: TestClient) -> None:
    for pair in (*DIFFERENT_BRAND_PAIRS, *SAME_PART_PAIRS, *DIFFERENT_PRODUCT_PAIRS):
        reason = _judge(client, pair.a, pair.b)["reason_fa"]
        assert reason.strip()
        assert contains_persian(reason), reason


def test_a_different_part_number_is_a_different_part(client: TestClient) -> None:
    body = _judge(
        client,
        "فیلتر روغن پژو 206 ایساکو کد 1109AY",
        "فیلتر روغن پژو 206 ایساکو کد 1109AZ",
    )
    assert body["is_same"] is False
    assert "شماره فنی" in body["reason_fa"]


def test_confidence_is_a_probability(client: TestClient) -> None:
    body = _judge(client, "لنت ترمز پراید", "لنت ترمز پراید")
    assert 0.0 <= body["confidence"] <= 1.0


def test_context_is_part_of_the_cache_key(client: TestClient) -> None:
    first = _judge(client, "لنت ترمز پراید", "لنت ترمز پرايد")
    second = _judge(client, "لنت ترمز پراید", "لنت ترمز پرايد", context={"cluster_uid": "abc"})

    assert first["cached"] is False
    assert second["cached"] is False, "a different context must not reuse an answer"


def test_the_second_identical_pair_is_served_from_cache(client: TestClient) -> None:
    first = _judge(client, "شمع موتور بوش", "شمع موتور بوش اصلی")
    second = _judge(client, "شمع موتور بوش", "شمع موتور بوش اصلی")

    assert first["cached"] is False
    assert second["cached"] is True
    assert first["is_same"] == second["is_same"]
    assert first["reason_fa"] == second["reason_fa"]
