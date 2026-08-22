from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field


class OfferOut(Schema):
    offer_uid: str
    seller_key: str
    seller_name: str
    is_panel_offer: bool = False
    price_toman: int | None
    stock_status: str
    authenticity_claim: str
    trust_score: float
    rank_position: int
    url: str
    is_cheapest: bool


class ProductHitOut(Schema):
    product_uid: UUID
    slug: str
    title: str
    brand: str | None = None
    part_type: str | None = None
    authenticity_dominant: str
    image_url: str | None = None
    min_price_toman: int | None = None
    max_price_toman: int | None = None
    median_price_toman: int | None = None
    offer_count: int
    offers: list[OfferOut]
    risky_family_note_fa: str | None = None
    fitment_status: Literal["fits", "unverified", "incompatible", "not_requested"]
    exact_part_number_match: bool = False


class FacetBucket(Schema):
    value: str
    count: int


class FacetsOut(Schema):
    vehicles: list[FacetBucket]
    brands: list[FacetBucket]
    part_types: list[FacetBucket]
    authenticity: list[FacetBucket]
    price_ranges: list[FacetBucket]


class SearchResponse(Schema):
    query_id: UUID
    normalized_query: str
    page: int
    page_size: int
    total: int
    fallback_applied: bool
    hits: list[ProductHitOut]
    facets: FacetsOut


class SuggestionOut(Schema):
    product_uid: UUID
    text: str
    part_number: str | None = None


class SuggestResponse(Schema):
    normalized_query: str
    suggestions: list[SuggestionOut]


class ClickEventIn(Schema):
    query_id: UUID
    product_uid: UUID
    position: int = Field(ge=1)


class AcceptedResponse(Schema):
    accepted: bool = True


class HealthResponse(Schema):
    status: Literal["ok", "degraded"]
    components: dict[str, str]


class ErrorResponse(Schema):
    code: str
    message: str
    detail: dict[str, Any] | None = None
