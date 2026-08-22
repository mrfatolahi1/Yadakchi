from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from billing.models import OutboxEvent, Seller

CLICKS_RECORDED_TOPIC = "yadakchi.clicks.recorded.v1"
SELLER_BILLING_CHANGED_TOPIC = "yadakchi.seller_billing.changed.v1"
REVIEW_REQUESTED_TOPIC = "yadakchi.review.requested.v1"


def utc_string(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def add_outbox_event(
    *,
    topic: str,
    message_key: str,
    natural_key: str,
    event_type: str,
    occurred_at: datetime,
    trace_id: str,
    payload: dict[str, Any] | None,
) -> OutboxEvent:
    event_id = uuid.uuid4()
    body = {
        "event_id": str(event_id),
        "event_type": event_type,
        "version": 1,
        "occurred_at": utc_string(occurred_at),
        "producer": "billing",
        "trace_id": trace_id,
        "payload": payload,
    }
    return OutboxEvent.objects.create(
        event_id=event_id,
        topic=topic,
        message_key=message_key,
        natural_key=natural_key,
        body=body,
    )


def add_seller_billing_state_event(
    seller: Seller, *, reason: str | None, occurred_at: datetime, trace_id: str
) -> OutboxEvent:
    return add_outbox_event(
        topic=SELLER_BILLING_CHANGED_TOPIC,
        message_key=seller.seller_key,
        natural_key=f"{seller.seller_key}:{seller.billing_state_version}",
        event_type="seller_billing.changed",
        occurred_at=occurred_at,
        trace_id=trace_id,
        payload={
            "seller_key": seller.seller_key,
            "panel_offers_active": seller.panel_offers_active,
            "suspension_reason": reason,
            "updated_at": utc_string(occurred_at),
        },
    )
