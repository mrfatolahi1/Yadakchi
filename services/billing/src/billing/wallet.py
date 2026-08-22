from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from billing.click_queue import QueuedClick
from billing.events import (
    CLICKS_RECORDED_TOPIC,
    REVIEW_REQUESTED_TOPIC,
    add_outbox_event,
    add_seller_billing_state_event,
    utc_string,
)
from billing.models import ClickEvent, Seller, WalletTransaction
from billing.rates import resolve_cpc_rate


class SellerUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClickProcessingResult:
    click: ClickEvent
    created: bool


def _occurred_at(value: str) -> datetime:
    parsed = parse_datetime(value)
    if parsed is None or not parsed.tzinfo:
        raise ValueError("occurred_at must be an ISO-8601 timezone-aware timestamp")
    return parsed


def _deactivate_panel_offers(
    seller: Seller, *, reason: str, occurred_at: datetime, trace_id: str
) -> None:
    if not seller.panel_offers_active:
        return
    seller.panel_offers_active = False
    seller.billing_state_version += 1
    seller.save(update_fields=["panel_offers_active", "billing_state_version", "updated_at"])
    add_seller_billing_state_event(
        seller, reason=reason, occurred_at=occurred_at, trace_id=trace_id
    )


@transaction.atomic
def process_queued_click(queued: QueuedClick) -> ClickProcessingResult:
    click_id = UUID(queued.click_id)
    existing = ClickEvent.objects.filter(click_id=click_id).first()
    if existing is not None:
        return ClickProcessingResult(click=existing, created=False)

    try:
        seller = Seller.objects.select_for_update().get(seller_key=queued.seller_key)
    except Seller.DoesNotExist as exc:
        raise SellerUnavailableError(queued.seller_key) from exc

    occurred_at = _occurred_at(queued.occurred_at)
    rate = resolve_cpc_rate(queued.price_toman, at=occurred_at)
    cost_toman = 0
    rate_cost = rate.cost_toman if rate is not None else 0

    chargeable = (
        queued.is_panel_offer
        and not queued.is_suspicious
        and seller.panel_offers_active
        and rate is not None
        and rate_cost > 0
    )
    if chargeable and seller.wallet_balance_toman >= rate_cost:
        cost_toman = rate_cost

    click = ClickEvent.objects.create(
        click_id=click_id,
        product_uid=UUID(queued.product_uid),
        offer_uid=queued.offer_uid,
        seller_key=queued.seller_key,
        price_toman=queued.price_toman,
        is_panel_offer=queued.is_panel_offer,
        cost_toman=cost_toman,
        is_suspicious=queued.is_suspicious,
        fraud_reasons=queued.fraud_reasons,
        ip_hash=queued.ip_hash,
        user_agent_hash=queued.user_agent_hash,
        fingerprint_hash=queued.fingerprint_hash,
        rate=rate,
        occurred_at=occurred_at,
    )

    if cost_toman:
        seller.wallet_balance_toman -= cost_toman
        seller.save(update_fields=["wallet_balance_toman", "updated_at"])
        WalletTransaction.objects.create(
            seller=seller,
            kind=WalletTransaction.Kind.CHARGE,
            amount_toman=-cost_toman,
            balance_after_toman=seller.wallet_balance_toman,
            click=click,
            occurred_at=occurred_at,
        )
        if seller.wallet_balance_toman == 0:
            _deactivate_panel_offers(
                seller,
                reason="zero_balance",
                occurred_at=occurred_at,
                trace_id=queued.trace_id,
            )
    elif chargeable and seller.wallet_balance_toman < rate_cost:
        # All-or-nothing CPC: preserve the residual balance and suspend panel offers.
        _deactivate_panel_offers(
            seller,
            reason="insufficient_balance",
            occurred_at=occurred_at,
            trace_id=queued.trace_id,
        )

    add_outbox_event(
        topic=CLICKS_RECORDED_TOPIC,
        message_key=queued.product_uid,
        natural_key=queued.click_id,
        event_type="clicks.recorded",
        occurred_at=occurred_at,
        trace_id=queued.trace_id,
        payload={
            "click_id": queued.click_id,
            "product_uid": queued.product_uid,
            "offer_uid": queued.offer_uid,
            "seller_key": queued.seller_key,
            "cost_toman": cost_toman,
            "is_suspicious": queued.is_suspicious,
            "occurred_at": utc_string(occurred_at),
        },
    )

    if queued.velocity_anomaly:
        request_uid = f"billing-velocity:{queued.seller_key}:{queued.velocity_bucket}"
        add_outbox_event(
            topic=REVIEW_REQUESTED_TOPIC,
            message_key=request_uid,
            natural_key=request_uid,
            event_type="review.requested",
            occurred_at=occurred_at,
            trace_id=queued.trace_id,
            payload={
                "request_uid": request_uid,
                "kind": "seller_click_velocity",
                "priority": queued.velocity_count,
                "subject": {"seller_key": queued.seller_key},
                "evidence": {
                    "click_count": queued.velocity_count,
                    "window_seconds": settings.SELLER_VELOCITY_WINDOW_SECONDS,
                    "rule": "per_seller_click_velocity",
                },
                "requested_at": utc_string(occurred_at),
            },
        )
    return ClickProcessingResult(click=click, created=True)


@transaction.atomic
def record_topup(
    *, seller_key: str, amount_toman: int, reference: str, occurred_at: datetime | None = None
) -> WalletTransaction:
    if amount_toman <= 0:
        raise ValueError("top-up amount must be positive")
    when = occurred_at or timezone.now()
    seller = Seller.objects.select_for_update().get(seller_key=seller_key)

    existing = WalletTransaction.objects.filter(reference=reference).first()
    if existing is not None:
        if existing.seller_id != seller_key or existing.amount_toman != amount_toman:
            raise ValueError("gateway reference already used for different top-up")
        return existing

    seller.wallet_balance_toman += amount_toman
    was_inactive = not seller.panel_offers_active
    if was_inactive:
        seller.panel_offers_active = True
        seller.billing_state_version += 1
    seller.save(
        update_fields=[
            "wallet_balance_toman",
            "panel_offers_active",
            "billing_state_version",
            "updated_at",
        ]
    )
    topup = WalletTransaction.objects.create(
        seller=seller,
        kind=WalletTransaction.Kind.TOPUP,
        amount_toman=amount_toman,
        balance_after_toman=seller.wallet_balance_toman,
        reference=reference,
        occurred_at=when,
    )
    if was_inactive:
        add_seller_billing_state_event(
            seller,
            reason=None,
            occurred_at=when,
            trace_id=str(topup.transaction_id),
        )
    return topup


@transaction.atomic
def adjust_wallet(
    *,
    seller_key: str,
    amount_toman: int,
    reference: str,
    occurred_at: datetime | None = None,
) -> WalletTransaction:
    if amount_toman == 0:
        raise ValueError("manual adjustment cannot be zero")
    when = occurred_at or timezone.now()
    seller = Seller.objects.select_for_update().get(seller_key=seller_key)
    if WalletTransaction.objects.filter(reference=reference).exists():
        raise ValueError("manual adjustment reference already exists")
    new_balance = seller.wallet_balance_toman + amount_toman
    if new_balance < 0:
        raise ValueError("manual adjustment cannot make the prepaid wallet negative")

    was_active = seller.panel_offers_active
    seller.wallet_balance_toman = new_balance
    if new_balance == 0:
        seller.panel_offers_active = False
    elif amount_toman > 0 and not was_active:
        seller.panel_offers_active = True
    state_changed = was_active != seller.panel_offers_active
    if state_changed:
        seller.billing_state_version += 1
    seller.save(
        update_fields=[
            "wallet_balance_toman",
            "panel_offers_active",
            "billing_state_version",
            "updated_at",
        ]
    )
    adjustment = WalletTransaction.objects.create(
        seller=seller,
        kind=WalletTransaction.Kind.MANUAL,
        amount_toman=amount_toman,
        balance_after_toman=new_balance,
        reference=reference,
        occurred_at=when,
    )
    if state_changed:
        add_seller_billing_state_event(
            seller,
            reason=None if seller.panel_offers_active else "zero_balance",
            occurred_at=when,
            trace_id=str(adjustment.transaction_id),
        )
    return adjustment
