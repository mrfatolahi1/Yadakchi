"""Request and response bodies — the published contract.

`enricher`, `matcher` and `search` generate their clients from the OpenAPI
document these produce, so a change here is a change to three other services.
Field names and shapes come straight from the spec; nothing extra is added
because "extra" means a vendored client somewhere else stops compiling.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai.config import MAX_EMBED_TEXTS

#: Persian, Arabic and the supplements — used to insist that `reason_fa` is
#: actually Persian, because a human reviewer in `ops` has to read it.
_PERSIAN_RANGE = ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


def contains_persian(text: str) -> bool:
    return any(any(low <= ord(char) <= high for low, high in _PERSIAN_RANGE) for char in text)


class ExtractRequest(BaseModel):
    """One listing title, plus the name of the shape to extract from it."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "text": "فیلتر روغن پژو 206 تیپ 5 اصلی ایساکو کد 1109AY",
                "schema_name": "offer_fields",
                "hint": None,
            }
        }
    )

    text: str = Field(min_length=1, max_length=4000, description="Listing title or description.")
    schema_name: str = Field(
        min_length=1,
        max_length=64,
        description="A registered output schema. Callers may not pass a prompt or a schema.",
        examples=["offer_fields"],
    )
    hint: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional extra context, e.g. the seller's category breadcrumb.",
    )


class ExtractResponse(BaseModel):
    """Every registered field is present; the ones the model did not produce
    are null with confidence 0.0."""

    fields: dict[str, Any] = Field(description="The registered schema, fully populated.")
    confidences: dict[str, float] = Field(description="Per-field confidence in [0, 1].")
    model: str = Field(description="Model that produced this answer.")
    cached: bool = Field(description="True when served from cache without reaching the model.")


class JudgeRequest(BaseModel):
    """Two listing titles to adjudicate."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "a": "لنت ترمز جلو پراید اصلی سایپا یدک",
                "b": "لنت ترمز جلو پراید برند تخت جمشید",
                "context": {"cluster_uid": "0f6a2f1e-6f7b-4a5e-9a6d-2b0e2f7c1a11"},
            }
        }
    )

    a: str = Field(min_length=1, max_length=4000)
    b: str = Field(min_length=1, max_length=4000)
    context: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context; it is part of the cache key.",
    )


class JudgeResponse(BaseModel):
    is_same: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason_fa: str = Field(
        min_length=1,
        description="Short Persian explanation, shown to human reviewers in `ops`.",
    )
    cached: bool


class EmbedRequest(BaseModel):
    texts: list[str] = Field(
        min_length=1,
        max_length=MAX_EMBED_TEXTS,
        description=f"Up to {MAX_EMBED_TEXTS} texts per call.",
    )

    @field_validator("texts")
    @classmethod
    def _no_oversized_text(cls, value: list[str]) -> list[str]:
        for text in value:
            if len(text) > 4000:
                raise ValueError("each text must be at most 4000 characters")
        return value


class EmbedResponse(BaseModel):
    vectors: list[list[float]] = Field(description="One vector per input text, in order.")
    # Written out rather than Literal[EMBEDDING_DIM] because a Literal needs a
    # real literal; `test_embed.py` asserts the two never drift apart.
    dim: Literal[384] = Field(
        default=384,
        description="Always 384. Vectors are never padded or truncated.",
    )
    model: str


class BudgetStatus(BaseModel):
    day: str = Field(description="UTC day the counter belongs to.")
    used: float
    limit: float
    ratio: float = Field(ge=0.0, le=1.0, description="Clamped; 1.0 means calls are refused.")
    unit: Literal["seconds", "currency"]
    enabled: bool
    exhausted: bool


class CacheStatus(BaseModel):
    redis: Literal["up", "down", "disabled"]
    entries: int = Field(description="Entries currently held by the in-process LRU.")
    ttl_seconds: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    backend: str
    model: str
    reachable: bool = Field(description="Whether the model provider answered a probe.")
    embed_backend: str
    embed_model: str
    dim: int
    prompt_version: str
    schemas: list[str]
    cache: CacheStatus
    budget: BudgetStatus


class ErrorResponse(BaseModel):
    """Every error this service returns, including 429 `budget_exhausted`."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "budget_exhausted",
                "message": "today's AI budget of 3600 seconds is spent",
                "detail": {"used": 3600.0, "limit": 3600.0, "unit": "seconds"},
            }
        }
    )

    code: str = Field(description="Stable machine-readable code. Callers switch on this.")
    message: str
    detail: dict[str, Any] | None = None
