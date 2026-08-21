"""The pieces around a model call: parsing, confidences, concurrency, logging."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pytest

from ai.api_models import ExtractRequest
from ai.backends.base import Completion, CompletionRequest, ModelBackend
from ai.backends.stub import StubBackend
from ai.budget import BudgetGuard
from ai.cache import Cache
from ai.config import Settings
from ai.embeddings import StubEmbedder
from ai.schemas import OFFER_FIELDS
from ai.service import AIService, _validate_extraction, parse_json_object, strip_fences


def build_service(settings: Settings, backend: ModelBackend | None = None) -> AIService:
    cache = Cache(settings)
    return AIService(
        settings,
        backend=backend or StubBackend(),
        embedder=StubEmbedder(),
        cache=cache,
        budget=BudgetGuard(settings, cache),
    )


# ------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '   {"a": 1}   ',
        'Here you go:\n{"a": 1}',
    ],
)
def test_a_model_answer_is_parsed_however_it_is_wrapped(raw: str) -> None:
    assert parse_json_object(raw) == {"a": 1}


def test_fences_are_stripped_without_touching_the_content() -> None:
    assert strip_fences('```json\n{"brand": "ایساکو"}\n```') == '{"brand": "ایساکو"}'


@pytest.mark.parametrize("raw", ["", "no json here", "[1, 2, 3]", '{"broken": '])
def test_an_unparseable_answer_raises(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_json_object(raw)


# --------------------------------------------------------------- confidences


def test_a_null_field_always_has_zero_confidence_whatever_the_model_claimed() -> None:
    fields, confidences = _validate_extraction(
        OFFER_FIELDS,
        {"fields": {"brand": None}, "confidences": {"brand": 0.99}},
    )
    assert fields["brand"] is None
    assert confidences["brand"] == 0.0


def test_an_empty_list_counts_as_no_value() -> None:
    _, confidences = _validate_extraction(
        OFFER_FIELDS,
        {"fields": {"vehicle_hints": []}, "confidences": {"vehicle_hints": 0.8}},
    )
    assert confidences["vehicle_hints"] == 0.0


def test_confidences_are_clamped_and_unknown_keys_dropped() -> None:
    fields, confidences = _validate_extraction(
        OFFER_FIELDS,
        {
            "fields": {"brand": "بوش", "part_number": "0242235666"},
            "confidences": {"brand": 3.5, "part_number": -1.0, "colour": 0.5},
        },
    )
    assert confidences["brand"] == 1.0
    assert confidences["part_number"] == 0.0
    assert "colour" not in confidences
    assert set(confidences) == set(fields)


def test_a_field_that_is_not_in_the_schema_is_rejected() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic's ValidationError
        _validate_extraction(OFFER_FIELDS, {"fields": {"colour": "red"}})


def test_a_missing_fields_object_is_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_extraction(OFFER_FIELDS, {"confidences": {}})


# --------------------------------------------------------------- concurrency


class SlowBackend(ModelBackend):
    """Counts how many calls are in flight at once."""

    name = "stub"

    def __init__(self) -> None:
        self.inflight = 0
        self.peak = 0

    @property
    def model_id(self) -> str:
        return "slow-1"

    async def complete(self, request: CompletionRequest) -> Completion:
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            await asyncio.sleep(0.01)
            return Completion(text='{"fields": {}, "confidences": {}}', model="slow-1")
        finally:
            self.inflight -= 1

    async def reachable(self) -> bool:
        return True


async def test_the_concurrency_cap_holds(make_settings: Callable[..., Settings]) -> None:
    """One caller must not be able to occupy a CPU-only host on its own."""
    backend = SlowBackend()
    service = build_service(make_settings(ai_max_concurrency=2), backend=backend)

    await asyncio.gather(
        *(
            service.extract(ExtractRequest(text=f"عنوان {index}", schema_name="offer_fields"))
            for index in range(8)
        )
    )

    assert backend.peak <= 2


# ------------------------------------------------------------------- logging


async def test_a_title_is_never_logged_above_debug(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    """Titles are business data: shape and digest at INFO, content only at DEBUG."""
    service = build_service(make_settings())
    title = "لنت ترمز جلو پراید اصلی سایپا یدک"

    with caplog.at_level(logging.INFO):
        await service.extract(ExtractRequest(text=title, schema_name="offer_fields"))

    for record in caplog.records:
        rendered = record.getMessage() + str(getattr(record, "prompt", ""))
        assert title not in rendered


async def test_debug_logging_does_include_the_prompt(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    service = build_service(make_settings())

    with caplog.at_level(logging.DEBUG, logger="ai.service"):
        await service.extract(ExtractRequest(text="لنت ترمز جلو پراید", schema_name="offer_fields"))

    prompts = [record for record in caplog.records if record.getMessage() == "model prompt"]
    assert prompts, "the full prompt must be available when someone asks for DEBUG"


# ----------------------------------------------------------------- responses


async def test_the_answer_shape_is_stable_for_enricher(
    make_settings: Callable[..., Settings],
) -> None:
    service = build_service(make_settings())
    answer = await service.extract(
        ExtractRequest(text="فیلتر روغن پژو 206 ایساکو کد 1109AY", schema_name="offer_fields")
    )
    payload: dict[str, Any] = answer.model_dump()

    assert set(payload) == {"fields", "confidences", "model", "cached"}
    assert set(payload["fields"]) == set(OFFER_FIELDS.field_names)
