from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from django.core.management import call_command


@pytest.fixture
def seeded(db: object) -> None:
    del db
    call_command("seed_vehicles")


@pytest.fixture
def offer_event() -> Callable[..., dict[str, Any]]:
    def build(
        *,
        offer_uid: str,
        seller_key: str = "seller-1",
        part_number: str | None = "425438",
        part_type: str | None = "brake_pad_front",
        brand: str | None = "ezam",
        vehicle_hints: list[str] | None = None,
        vehicle_hints_excluded: list[str] | None = None,
        overbroad_claim: bool = False,
        title: str = "لنت ترمز پژو 206 تیپ 5 کد 425438",
        price_toman: int | None = 1_000_000,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "event_id": event_id or str(uuid4()),
            "event_type": "offers.enriched",
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "producer": "enricher",
            "trace_id": uuid4().hex,
            "payload": {
                "offer_uid": offer_uid,
                "source_key": "source",
                "external_key": offer_uid,
                "seller_key": seller_key,
                "url": f"https://example.com/{offer_uid}",
                "raw_title": title,
                "title_normalized": title,
                "brand": brand,
                "part_number": part_number,
                "part_type": part_type,
                "authenticity_claim": "unknown",
                "pack_quantity": 1,
                "price_toman": price_toman,
                "stock_status": "in_stock",
                "vehicle_hints": (["پژو 206 تیپ 5"] if vehicle_hints is None else vehicle_hints),
                "vehicle_hints_excluded": (
                    [] if vehicle_hints_excluded is None else vehicle_hints_excluded
                ),
                "overbroad_claim": overbroad_claim,
                "confidences": {},
                "extraction_provenance": {},
                "normalizer_version": "1",
                "first_seen_at": "2026-08-01T00:00:00Z",
                "last_seen_at": "2026-08-01T00:00:00Z",
                "is_active": True,
            },
        }

    return build


@pytest.fixture
def decision_event() -> Callable[..., dict[str, Any]]:
    def build(
        *,
        part_number: str,
        vehicle_slug: str,
        status: str,
        request_uid: str = "fitment-review-1",
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return {
            "event_id": str(uuid4()),
            "event_type": "review.decided",
            "version": 1,
            "occurred_at": now,
            "producer": "ops",
            "trace_id": uuid4().hex,
            "payload": {
                "request_uid": request_uid,
                "kind": "fitment_conflict",
                "decision": "approve",
                "subject": {
                    "part_number": part_number,
                    "vehicle_slug": vehicle_slug,
                    "status": status,
                },
                "actor": "reviewer",
                "reason": "checked against seller evidence",
                "decided_at": now,
            },
        }

    return build
