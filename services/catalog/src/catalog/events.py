"""Pydantic v2 models for every event this service reads and writes.

These mirror ``contracts/`` field for field. Unknown fields are ignored
rather than rejected — that is what lets a producer add an optional field
without breaking us — but no field is ever *invented* here: if a value is not
in the consumed schema, it does not appear below.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AuthenticityLiteral = Literal["genuine", "oem", "aftermarket", "used", "refurbished", "unknown"]
StockLiteral = Literal["in_stock", "out_of_stock", "unknown"]
FitmentLiteral = Literal["compatible", "incompatible", "unknown"]
ProvenanceLiteral = Literal["rule", "model", "human", "catalog", "consensus"]
TierLiteral = Literal["new", "standard", "trusted", "suspended"]

OfferUid = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
VehicleSlug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
SellerKey = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# ================================================================= envelope
class Envelope(_Base):
    """The shared envelope every topic carries.

    ``payload`` stays a raw mapping here so a consumer can check the envelope
    (and record the duplicate guard) before paying to validate the body, and
    so a tombstone — ``payload: null`` on a compacted topic — parses cleanly.
    """

    event_id: uuid.UUID
    event_type: str
    version: int
    occurred_at: dt.datetime
    producer: str
    trace_id: str
    payload: dict[str, Any] | None = None

    @property
    def is_tombstone(self) -> bool:
        return self.payload is None


# ============================================================ consumed: matcher
class ClusterMember(_Base):
    offer_uid: OfferUid
    confidence: float
    provenance: ProvenanceLiteral


class ClustersChangedPayload(_Base):
    cluster_uid: uuid.UUID
    members: list[ClusterMember]
    change_reason: str
    predecessor_uids: list[uuid.UUID] = Field(default_factory=list)
    successor_uid: uuid.UUID | None = None
    computed_at: dt.datetime


# =========================================================== consumed: enricher
class OffersEnrichedPayload(_Base):
    offer_uid: OfferUid
    source_key: str
    external_key: str
    seller_key: SellerKey
    url: str
    raw_title: str
    title_normalized: str
    brand: str | None = None
    part_number: str | None = None
    part_type: str | None = None
    authenticity_claim: AuthenticityLiteral
    pack_quantity: int = 1
    price_toman: int | None = None
    stock_status: StockLiteral
    image_url: str | None = None
    vehicle_hints: list[str] = Field(default_factory=list)
    #: Optional and additive. Absent or empty means "no negative claim was
    #: extracted" — never "fits everything".
    vehicle_hints_excluded: list[str] = Field(default_factory=list)
    overbroad_claim: bool = False
    confidences: dict[str, float] = Field(default_factory=dict)
    extraction_provenance: dict[str, str] = Field(default_factory=dict)
    normalizer_version: str
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    is_active: bool


# =========================================================== consumed: fitment
class FitmentVerdict(_Base):
    vehicle_slug: VehicleSlug
    status: FitmentLiteral
    confidence: float
    provenance: ProvenanceLiteral
    evidence: dict[str, Any] = Field(default_factory=dict)


class RiskyFamily(_Base):
    part_type: str
    required_granularity: str
    note_fa: str


class OffersFittedPayload(_Base):
    offer_uid: OfferUid
    fitments: list[FitmentVerdict] = Field(default_factory=list)
    crossref_codes: list[str] = Field(default_factory=list)
    risky_family: RiskyFamily | None = None
    computed_at: dt.datetime


class VehiclesChangedPayload(_Base):
    vehicle_slug: VehicleSlug
    brand: str
    model: str
    trim: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    engine_code: str | None = None
    display_name_fa: str
    aliases: list[str] = Field(default_factory=list)
    is_published: bool
    updated_at: dt.datetime


class CrossRefsChangedPayload(_Base):
    code_a: str
    code_b: str
    brand_a: str | None = None
    brand_b: str | None = None
    confidence: float
    provenance: ProvenanceLiteral
    updated_at: dt.datetime


# =========================================================== consumed: billing
class ClicksRecordedPayload(_Base):
    click_id: str
    product_uid: uuid.UUID
    offer_uid: OfferUid
    seller_key: SellerKey
    cost_toman: int
    is_suspicious: bool
    occurred_at: dt.datetime


# =============================================== published: products.changed
class ProductOfferOut(_Base):
    offer_uid: OfferUid
    seller_key: SellerKey
    seller_name: str
    price_toman: int | None
    stock_status: StockLiteral
    authenticity_claim: AuthenticityLiteral
    trust_score: float
    rank_position: int
    url: str
    is_cheapest: bool


class PricePointOut(_Base):
    date: str
    min_toman: int
    median_toman: int


class ProductsChangedPayload(_Base):
    product_uid: str
    slug: str
    title: str
    brand: str | None
    part_type: str | None
    authenticity_dominant: AuthenticityLiteral
    image_url: str | None
    part_numbers: list[str]
    crossref_codes: list[str]
    vehicles_compatible: list[VehicleSlug]
    vehicles_incompatible: list[VehicleSlug]
    vehicles_unknown: list[VehicleSlug]
    risky_family_note_fa: str | None
    offer_count: int
    min_price_toman: int | None
    max_price_toman: int | None
    median_price_toman: int | None
    offers: list[ProductOfferOut]
    price_series: list[PricePointOut]
    is_published: bool
    successor_product_uid: str | None
    updated_at: str


# ================================================ published: sellers.changed
class SellersChangedPayload(_Base):
    seller_key: SellerKey
    name: str
    domain: str
    source_key: str | None
    is_panel: bool
    tier: TierLiteral
    trust_score: float
    price_accuracy: float | None
    stock_accuracy: float | None
    updated_at: str


def iso_utc(value: dt.datetime) -> str:
    """ISO-8601 UTC with a literal Z, which is what every schema's pattern
    demands. Microseconds are dropped: the contract allows 0-6 digits and a
    stable string keeps payload hashing stable."""
    return value.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
