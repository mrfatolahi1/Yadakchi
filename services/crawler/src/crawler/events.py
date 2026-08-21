from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ListingsObservedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_key: str
    external_key: str
    url: str
    raw_title: str
    raw_price_text: str | None = None
    raw_stock_text: str | None = None
    image_url: str | None = None
    raw_fragment: str
    archive_uri: str
    fragment_hash: str
    observed_at: datetime


class ListingsObservedEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: UUID
    event_type: Literal["listings.observed"] = "listings.observed"
    version: Literal[2] = 2
    occurred_at: datetime
    producer: Literal["crawler"] = "crawler"
    trace_id: str
    payload: ListingsObservedPayload


class ReviewRequestedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_uid: str
    kind: str
    priority: int
    subject: dict[str, Any]
    evidence: dict[str, Any]
    requested_at: datetime


class ReviewRequestedEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: UUID
    event_type: Literal["review.requested"] = "review.requested"
    version: Literal[1] = 1
    occurred_at: datetime
    producer: Literal["crawler"] = "crawler"
    trace_id: str = Field(min_length=1)
    payload: ReviewRequestedPayload


class ClickRecordedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    click_id: str = Field(min_length=1)
    product_uid: UUID
    offer_uid: str = Field(pattern=r"^[0-9a-f]{32}$")
    seller_key: str
    cost_toman: int = Field(ge=0)
    is_suspicious: bool
    occurred_at: datetime


class ClickRecordedEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: UUID
    event_type: Literal["clicks.recorded"]
    version: Literal[1]
    occurred_at: datetime
    producer: Literal["billing"]
    trace_id: str = Field(min_length=1)
    payload: ClickRecordedPayload
