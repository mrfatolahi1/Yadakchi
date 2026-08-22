from __future__ import annotations

import json
from pathlib import Path

import pytest

from search.text import looks_like_part_number, normalize_part_number, normalize_text

VECTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "normalization-vectors.json").read_text()
)


@pytest.mark.parametrize("vector", VECTORS["normalize_text"], ids=lambda value: value["name"])
def test_index_and_query_normalization_are_symmetric(vector: dict[str, object]) -> None:
    normalized = normalize_text(str(vector["input"]))
    assert normalized == vector["expected"]
    assert normalize_text(normalized) == vector["expected"]


@pytest.mark.parametrize(
    "vector", VECTORS["normalize_part_number"], ids=lambda value: value["name"]
)
def test_part_number_normalization_vectors(vector: dict[str, object]) -> None:
    assert normalize_part_number(str(vector["input"])) == vector["expected"]


@pytest.mark.parametrize(
    "vector", VECTORS["looks_like_part_number"], ids=lambda value: value["name"]
)
def test_part_number_shape_vectors(vector: dict[str, object]) -> None:
    assert looks_like_part_number(str(vector["input"])) is vector["expected"]


def test_part_number_fast_path_requires_the_whole_query_to_be_a_code() -> None:
    assert not looks_like_part_number("لنت 425438")
