"""Django Ninja schemas for the read API.

These mirror ``products.changed.v1`` exactly, plus the two things the event
deliberately does not carry — related parts and the per-seller badge — which
`web` needs to render a page but which have no field in the published payload
and are therefore served here instead of being bolted onto a versioned
contract.
"""

from __future__ import annotations

from typing import Any

from ninja import Schema
from pydantic import Field


class OfferOut(Schema):
    offer_uid: str
    seller_key: str
    seller_name: str
    price_toman: int | None = None
    stock_status: str
    authenticity_claim: str
    trust_score: float
    rank_position: int
    url: str
    is_cheapest: bool


class PricePointOut(Schema):
    date: str
    min_toman: int
    median_toman: int


class RelatedProductOut(Schema):
    product_uid: str
    slug: str
    title: str
    image_url: str | None = None
    min_price_toman: int | None = None
    offer_count: int


class SellerBadgeOut(Schema):
    tier: str
    is_new_seller: bool
    trust_score: float


class ProductOut(Schema):
    """The whole renderable product, as `web` gets it."""

    product_uid: str
    slug: str
    title: str
    brand: str | None = None
    part_type: str | None = None
    authenticity_dominant: str
    image_url: str | None = None
    part_numbers: list[str] = Field(default_factory=list)
    crossref_codes: list[str] = Field(default_factory=list)
    vehicles_compatible: list[str] = Field(default_factory=list)
    vehicles_incompatible: list[str] = Field(default_factory=list)
    vehicles_unknown: list[str] = Field(default_factory=list)
    risky_family_note_fa: str | None = None
    offer_count: int
    min_price_toman: int | None = None
    max_price_toman: int | None = None
    median_price_toman: int | None = None
    offers: list[OfferOut] = Field(default_factory=list)
    price_series: list[PricePointOut] = Field(default_factory=list)
    is_published: bool
    successor_product_uid: str | None = None
    updated_at: str

    #: Read-API only, not on the event.
    related_products: list[RelatedProductOut] = Field(default_factory=list)
    seller_badges: dict[str, SellerBadgeOut] = Field(default_factory=dict)

    @staticmethod
    def from_product(document: dict[str, Any], related: Any, badges: Any) -> dict[str, Any]:
        return {**document, "related_products": related or [], "seller_badges": badges or {}}


class RedirectOut(Schema):
    """A retired product, or an old slug. Both are 301s, and both exist so a
    URL that once ranked keeps its value instead of turning into a 404."""

    status: str
    product_uid: str
    redirect_to_slug: str
    successor_product_uid: str | None = None


class BatchRequest(Schema):
    product_uids: list[str] = Field(..., min_length=1, max_length=200)


class BatchResponse(Schema):
    products: list[ProductOut] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class SellerOut(Schema):
    """Seller profile for the ops console."""

    seller_key: str
    name: str
    domain: str
    source_key: str | None = None
    is_panel: bool
    tier: str
    tier_override: str | None = None
    trust_score: float
    price_accuracy: float | None = None
    stock_accuracy: float | None = None
    price_observations: int
    price_hits: int
    stock_observations: int
    stock_hits: int
    domain_age_days: int | None = None
    contact_completeness: float | None = None
    has_trust_badge: bool | None = None
    is_new_seller: bool
    product_count: int
    updated_at: str


class HealthOut(Schema):
    status: str
    service: str
    database: str
