from __future__ import annotations

from datetime import datetime

from django.db.models import Q, QuerySet
from django.utils import timezone

from billing.models import CpcRate


def active_rates(at: datetime | None = None) -> QuerySet[CpcRate]:
    effective_at = at or timezone.now()
    return CpcRate.objects.filter(active=True, effective_from__lte=effective_at).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gt=effective_at)
    )


def resolve_cpc_rate(price_toman: int | None, *, at: datetime | None = None) -> CpcRate | None:
    rates = active_rates(at)
    if price_toman is None:
        return rates.order_by("min_price_toman", "-effective_from").first()
    return (
        rates.filter(min_price_toman__lte=price_toman)
        .filter(Q(max_price_toman__isnull=True) | Q(max_price_toman__gt=price_toman))
        .order_by("-min_price_toman", "-effective_from")
        .first()
    )
