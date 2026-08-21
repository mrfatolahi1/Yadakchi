from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: UUID
    event_type: str
    version: Literal[1]
    occurred_at: datetime
    producer: str
    trace_id: str
    payload: dict[str, Any] | None


class EnrichedOfferPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    offer_uid: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_key: str
    external_key: str
    seller_key: str
    url: str
    raw_title: str
    title_normalized: str
    brand: str | None = None
    part_number: str | None = None
    part_type: str | None = None
    price_toman: int | None = None
    vehicle_hints: list[str]
    vehicle_hints_excluded: list[str] = Field(default_factory=list)
    overbroad_claim: bool
    is_active: bool


class ReviewDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_uid: str
    kind: str
    decision: str
    subject: dict[str, Any]
    actor: str
    reason: str | None = None
    decided_at: datetime


class FitmentEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")


class FittedEntry(BaseModel):
    vehicle_slug: str
    status: Literal["compatible", "incompatible", "unknown"]
    confidence: float = Field(ge=0, le=1)
    provenance: Literal["rule", "model", "human", "catalog", "consensus"]
    evidence: dict[str, Any]


class RiskyFamilyPayload(BaseModel):
    part_type: str
    required_granularity: str
    note_fa: str


class OffersFittedPayload(BaseModel):
    offer_uid: str
    fitments: list[FittedEntry]
    crossref_codes: list[str]
    risky_family: RiskyFamilyPayload | None
    computed_at: datetime
