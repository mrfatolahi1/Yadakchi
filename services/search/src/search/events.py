from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict


class EventModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Offer(EventModel):
    offer_uid: str
    seller_key: str
    seller_name: str
    title_normalized: str | None = None
    is_panel_offer: bool = False
    price_toman: int | None
    price_observed_at: AwareDatetime | None = None
    stock_status: Literal["in_stock", "out_of_stock", "unknown"]
    authenticity_claim: Literal["genuine", "oem", "aftermarket", "used", "refurbished", "unknown"]
    trust_score: float
    rank_position: int
    url: str
    is_cheapest: bool


class PricePoint(EventModel):
    date: date
    min_toman: int
    median_toman: int


class ProductPayload(EventModel):
    product_uid: UUID
    slug: str
    title: str
    brand: str | None = None
    part_type: str | None = None
    authenticity_dominant: Literal[
        "genuine", "oem", "aftermarket", "used", "refurbished", "unknown"
    ]
    image_url: str | None = None
    part_numbers: list[str]
    crossref_codes: list[str]
    vehicles_compatible: list[str]
    vehicles_incompatible: list[str]
    vehicles_unknown: list[str]
    risky_family_note_fa: str | None = None
    offer_count: int
    min_price_toman: int | None = None
    max_price_toman: int | None = None
    median_price_toman: int | None = None
    offers: list[Offer]
    price_series: list[PricePoint]
    is_published: bool
    successor_product_uid: UUID | None = None
    updated_at: AwareDatetime


class ProductEvent(EventModel):
    event_id: UUID
    event_type: Literal["products.changed"]
    version: Literal[1]
    occurred_at: AwareDatetime
    producer: Literal["catalog"]
    trace_id: str
    payload: ProductPayload | None


class VehiclePayload(EventModel):
    vehicle_slug: str
    brand: str
    model: str
    trim: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    engine_code: str | None = None
    display_name_fa: str
    aliases: list[str]
    is_published: bool
    updated_at: AwareDatetime


class VehicleEvent(EventModel):
    event_id: UUID
    event_type: Literal["vehicles.changed"]
    version: Literal[1]
    occurred_at: AwareDatetime
    producer: Literal["fitment"]
    trace_id: str
    payload: VehiclePayload | None


class CrossReferencePayload(EventModel):
    code_a: str
    code_b: str
    brand_a: str | None = None
    brand_b: str | None = None
    confidence: float
    provenance: Literal["rule", "model", "human", "catalog", "consensus"]
    updated_at: AwareDatetime


class CrossReferenceEvent(EventModel):
    event_id: UUID
    event_type: Literal["crossrefs.changed"]
    version: Literal[1]
    occurred_at: AwareDatetime
    producer: Literal["fitment"]
    trace_id: str
    payload: CrossReferencePayload | None


class ReviewPayload(EventModel):
    request_uid: str
    kind: str
    decision: str
    subject: dict[str, Any]
    actor: str
    reason: str | None = None
    decided_at: AwareDatetime


class ReviewEvent(EventModel):
    event_id: UUID
    event_type: Literal["review.decided"]
    version: Literal[1]
    occurred_at: AwareDatetime
    producer: Literal["ops"]
    trace_id: str
    payload: ReviewPayload | None


Event = ProductEvent | VehicleEvent | CrossReferenceEvent | ReviewEvent
