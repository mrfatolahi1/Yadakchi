from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

BASE_TIME = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def product_payload(
    product_uid: UUID | None = None,
    *,
    title: str = "لنت ترمز جلو پژو 206",
    part_numbers: list[str] | None = None,
    compatible: list[str] | None = None,
    incompatible: list[str] | None = None,
    unknown: list[str] | None = None,
    brand: str | None = "isaco",
    part_type: str | None = "front_brake_pad",
    authenticity: str = "oem",
    min_price: int | None = 900_000,
    image: bool = True,
    is_published: bool = True,
    updated_at: datetime = BASE_TIME,
) -> dict[str, Any]:
    uid = product_uid or uuid4()
    offer_uid = "a" * 32
    return {
        "product_uid": str(uid),
        "slug": f"product-{str(uid)[:8]}",
        "title": title,
        "brand": brand,
        "part_type": part_type,
        "authenticity_dominant": authenticity,
        "image_url": "https://example.test/image.jpg" if image else None,
        "part_numbers": part_numbers or ["425438"],
        "crossref_codes": [],
        "vehicles_compatible": compatible or [],
        "vehicles_incompatible": incompatible or [],
        "vehicles_unknown": unknown or [],
        "risky_family_note_fa": None,
        "offer_count": 1,
        "min_price_toman": min_price,
        "max_price_toman": min_price,
        "median_price_toman": min_price,
        "offers": [
            {
                "offer_uid": offer_uid,
                "seller_key": "seller-one",
                "seller_name": "فروشنده یک",
                "title_normalized": title,
                "price_toman": min_price,
                "price_observed_at": updated_at.isoformat().replace("+00:00", "Z"),
                "stock_status": "in_stock",
                "authenticity_claim": authenticity,
                "trust_score": 0.8,
                "rank_position": 1,
                "url": "https://example.test/offer",
                "is_cheapest": True,
            }
        ],
        "price_series": [
            {
                "date": updated_at.date().isoformat(),
                "min_toman": min_price or 0,
                "median_toman": min_price or 0,
            }
        ],
        "is_published": is_published,
        "successor_product_uid": None,
        "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
    }


def product_event(
    payload: dict[str, Any] | None,
    *,
    occurred_at: datetime = BASE_TIME,
    event_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "event_id": str(event_id or uuid4()),
        "event_type": "products.changed",
        "version": 1,
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "producer": "catalog",
        "trace_id": "test-trace",
        "payload": deepcopy(payload),
    }


def later(seconds: int = 1) -> datetime:
    return BASE_TIME + timedelta(seconds=seconds)


def review_event(
    *,
    decision: str,
    token: str = "توپی چرخ",
    part_type: str = "wheel_bearing",
    request_uid: str = "synonym-1",
    occurred_at: datetime = BASE_TIME,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "event_type": "review.decided",
        "version": 1,
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "producer": "ops",
        "trace_id": "test-trace",
        "payload": {
            "request_uid": request_uid,
            "kind": "synonym_candidate",
            "decision": decision,
            "subject": {"token": token, "part_type": part_type},
            "actor": "reviewer@example.test",
            "reason": None,
            "decided_at": occurred_at.isoformat().replace("+00:00", "Z"),
        },
    }
