from __future__ import annotations

import hmac
from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from ninja.security import HttpBearer
from pydantic import Field

from billing.models import Seller, WalletTransaction
from billing.rates import active_rates
from billing.reporting import seller_stats
from billing.wallet import record_topup


class InternalBearer(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> str | None:
        expected = settings.INTERNAL_API_TOKEN
        if expected and hmac.compare_digest(token, expected):
            return token
        return None


api = NinjaAPI(
    title="Yadakchi Billing API",
    version="1.0.0",
    description="Internal seller billing and reporting API. Ranking is never influenced here.",
    auth=InternalBearer(),
    urls_namespace="billing-api",
)


class DailyStatsSchema(Schema):
    day: date
    clicks: int
    suspicious_clicks: int
    charged_clicks: int
    spend_toman: int


class SellerStatsSchema(Schema):
    seller_key: str
    total_clicks: int
    suspicious_clicks: int
    charged_clicks: int
    spend_toman: int
    daily: list[DailyStatsSchema]


class WalletTransactionSchema(Schema):
    transaction_id: UUID
    kind: str
    amount_toman: int
    balance_after_toman: int
    reference: str | None
    occurred_at: datetime


class WalletSchema(Schema):
    seller_key: str
    balance_toman: int
    panel_offers_active: bool
    transactions: list[WalletTransactionSchema]


class TopupIn(Schema):
    amount_toman: int = Field(gt=0)
    gateway_reference: str = Field(min_length=1, max_length=255)


class RateSchema(Schema):
    id: int
    name: str
    min_price_toman: int
    max_price_toman: int | None
    cost_toman: int
    effective_from: datetime
    effective_to: datetime | None


@api.get("/v1/sellers/{seller_key}/stats", response=SellerStatsSchema)
def stats_endpoint(
    request: HttpRequest,
    seller_key: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    del request
    if not Seller.objects.filter(seller_key=seller_key).exists():
        raise HttpError(404, "seller not found")
    stats = seller_stats(seller_key, start=start, end=end)
    return {
        "seller_key": seller_key,
        "total_clicks": stats.total_clicks,
        "suspicious_clicks": stats.suspicious_clicks,
        "charged_clicks": stats.charged_clicks,
        "spend_toman": stats.spend_toman,
        "daily": [item.__dict__ for item in stats.daily],
    }


@api.get("/v1/sellers/{seller_key}/wallet", response=WalletSchema)
def wallet_endpoint(request: HttpRequest, seller_key: str) -> dict[str, Any]:
    del request
    try:
        seller = Seller.objects.get(seller_key=seller_key)
    except Seller.DoesNotExist as exc:
        raise HttpError(404, "seller not found") from exc
    transactions = seller.transactions.all()[:100]
    return {
        "seller_key": seller_key,
        "balance_toman": seller.wallet_balance_toman,
        "panel_offers_active": seller.panel_offers_active,
        "transactions": [
            {
                "transaction_id": item.transaction_id,
                "kind": item.kind,
                "amount_toman": item.amount_toman,
                "balance_after_toman": item.balance_after_toman,
                "reference": item.reference,
                "occurred_at": item.occurred_at,
            }
            for item in transactions
        ],
    }


@api.post("/v1/sellers/{seller_key}/topup", response=WalletTransactionSchema)
def topup_endpoint(request: HttpRequest, seller_key: str, payload: TopupIn) -> WalletTransaction:
    del request
    try:
        return record_topup(
            seller_key=seller_key,
            amount_toman=payload.amount_toman,
            reference=payload.gateway_reference,
        )
    except Seller.DoesNotExist as exc:
        raise HttpError(404, "seller not found") from exc
    except ValueError as exc:
        raise HttpError(409, str(exc)) from exc


@api.get("/v1/rates", response=list[RateSchema])
def rates_endpoint(request: HttpRequest) -> list[dict[str, Any]]:
    del request
    return [
        {
            "id": rate.pk,
            "name": rate.name,
            "min_price_toman": rate.min_price_toman,
            "max_price_toman": rate.max_price_toman,
            "cost_toman": rate.cost_toman,
            "effective_from": rate.effective_from,
            "effective_to": rate.effective_to,
        }
        for rate in active_rates()
    ]
