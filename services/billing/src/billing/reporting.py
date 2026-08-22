from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from billing.models import ClickEvent, WalletTransaction


@dataclass(frozen=True)
class DailyStats:
    day: date
    clicks: int
    suspicious_clicks: int
    charged_clicks: int
    spend_toman: int


@dataclass(frozen=True)
class SellerStats:
    total_clicks: int
    suspicious_clicks: int
    charged_clicks: int
    spend_toman: int
    daily: list[DailyStats]


def seller_stats(
    seller_key: str, *, start: datetime | None = None, end: datetime | None = None
) -> SellerStats:
    clicks = ClickEvent.objects.filter(seller_key=seller_key)
    if start is not None:
        clicks = clicks.filter(occurred_at__gte=start)
    if end is not None:
        clicks = clicks.filter(occurred_at__lt=end)
    totals = clicks.aggregate(
        total_clicks=Count("click_id"),
        suspicious_clicks=Count("click_id", filter=Q(is_suspicious=True)),
        charged_clicks=Count("click_id", filter=Q(cost_toman__gt=0)),
        spend_toman=Sum("cost_toman", default=0),
    )
    rows = (
        clicks.annotate(day=TruncDate("occurred_at"))
        .values("day")
        .annotate(
            clicks=Count("click_id"),
            suspicious_clicks=Count("click_id", filter=Q(is_suspicious=True)),
            charged_clicks=Count("click_id", filter=Q(cost_toman__gt=0)),
            spend_toman=Sum("cost_toman", default=0),
        )
        .order_by("day")
    )
    daily = [
        DailyStats(
            day=row["day"],
            clicks=row["clicks"],
            suspicious_clicks=row["suspicious_clicks"],
            charged_clicks=row["charged_clicks"],
            spend_toman=row["spend_toman"],
        )
        for row in rows
    ]
    return SellerStats(daily=daily, **totals)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, start + timedelta(days=1)


def reconcile_day(day: date) -> list[str]:
    start, end = day_bounds(day)
    discrepancies: list[str] = []
    click_spend = (
        ClickEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=end).aggregate(
            total=Sum("cost_toman", default=0)
        )["total"]
        or 0
    )
    transaction_spend = -(
        WalletTransaction.objects.filter(
            kind=WalletTransaction.Kind.CHARGE,
            occurred_at__gte=start,
            occurred_at__lt=end,
        ).aggregate(total=Sum("amount_toman", default=0))["total"]
        or 0
    )
    if click_spend != transaction_spend:
        discrepancies.append(
            f"daily click spend {click_spend} != charge transactions {transaction_spend}"
        )

    charged_without_transaction = ClickEvent.objects.filter(
        occurred_at__gte=start,
        occurred_at__lt=end,
        cost_toman__gt=0,
        wallet_transaction__isnull=True,
    ).count()
    if charged_without_transaction:
        discrepancies.append(f"{charged_without_transaction} charged clicks lack a transaction")

    charge_rows = WalletTransaction.objects.filter(
        kind=WalletTransaction.Kind.CHARGE,
        occurred_at__gte=start,
        occurred_at__lt=end,
    ).select_related("click")
    mismatched_charges = 0
    for item in charge_rows:
        if (
            item.click_id is None
            or item.click is None
            or -item.amount_toman != item.click.cost_toman
        ):
            mismatched_charges += 1
    if mismatched_charges:
        discrepancies.append(f"{mismatched_charges} charge transactions differ from click cost")
    return discrepancies
