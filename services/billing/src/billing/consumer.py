from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime
from jsonschema import Draft202012Validator, FormatChecker

from billing.events import SELLER_BILLING_CHANGED_TOPIC, add_outbox_event
from billing.models import ProcessedEvent, Seller

SELLERS_CHANGED_TOPIC = "yadakchi.sellers.changed.v1"


@lru_cache(maxsize=1)
def seller_event_validator() -> Draft202012Validator:
    contract_path = (
        settings.BASE_DIR / "contracts" / "consumed" / "yadakchi.sellers.changed.v1.json"
    )
    schema: dict[str, Any] = json.loads(Path(contract_path).read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _timestamp(value: str) -> datetime:
    parsed = parse_datetime(value)
    if parsed is None or not parsed.tzinfo:
        raise ValueError("seller updated_at must be timezone-aware")
    return parsed


@transaction.atomic
def process_seller_event(body: dict[str, Any], *, message_key: str) -> bool:
    seller_event_validator().validate(body)
    event_id = UUID(body["event_id"])
    if ProcessedEvent.objects.filter(event_id=event_id).exists():
        return False

    payload = body["payload"]
    natural_key = message_key
    if payload is None:
        occurred_at = _timestamp(body["occurred_at"])
        seller = Seller.objects.select_for_update().filter(seller_key=message_key).first()
        tombstone_applied = False
        if seller is not None and (
            seller.source_updated_at is None or occurred_at > seller.source_updated_at
        ):
            seller.is_deleted = True
            seller.is_panel = False
            seller.panel_offers_active = False
            seller.source_updated_at = occurred_at
            seller.save(
                update_fields=[
                    "is_deleted",
                    "is_panel",
                    "panel_offers_active",
                    "source_updated_at",
                    "updated_at",
                ]
            )
            tombstone_applied = True
        if tombstone_applied:
            add_outbox_event(
                topic=SELLER_BILLING_CHANGED_TOPIC,
                message_key=message_key,
                natural_key=f"tombstone:{message_key}:{event_id}",
                event_type="seller_billing.changed",
                occurred_at=occurred_at,
                trace_id=str(body["trace_id"]),
                payload=None,
            )
    else:
        seller_key = str(payload["seller_key"])
        if seller_key != message_key:
            raise ValueError("Kafka key does not match payload seller_key")
        updated_at = _timestamp(payload["updated_at"])
        current = Seller.objects.select_for_update().filter(seller_key=seller_key).first()
        if (
            current is None
            or current.source_updated_at is None
            or updated_at > current.source_updated_at
        ):
            defaults = {
                "name": str(payload["name"]),
                "domain": str(payload["domain"]),
                "source_key": payload.get("source_key"),
                "is_panel": bool(payload["is_panel"]),
                "tier": str(payload["tier"]),
                "trust_score": Decimal(str(payload["trust_score"])),
                "price_accuracy": (
                    Decimal(str(payload["price_accuracy"]))
                    if payload.get("price_accuracy") is not None
                    else None
                ),
                "stock_accuracy": (
                    Decimal(str(payload["stock_accuracy"]))
                    if payload.get("stock_accuracy") is not None
                    else None
                ),
                "source_updated_at": updated_at,
                "is_deleted": False,
            }
            Seller.objects.update_or_create(seller_key=seller_key, defaults=defaults)

    ProcessedEvent.objects.create(
        event_id=event_id,
        topic=SELLERS_CHANGED_TOPIC,
        natural_key=natural_key,
    )
    return True
