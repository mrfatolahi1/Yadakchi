from datetime import timedelta

from django.db.models import Count, Max, OuterRef, Subquery
from django.utils import timezone

from crawler.adapters import get_adapter
from crawler.models import ClickSignal, CrawlCursor, Observation, Source

TIERS = ("hot", "warm", "cold", "discovery", "dormant")


def urls_for_tier(source: Source, tier: str) -> list[str]:
    if tier not in TIERS:
        raise ValueError(f"Unknown crawl tier {tier!r}")
    if tier == "discovery":
        return list(get_adapter(source.adapter_key).discover(source))

    latest_ids = (
        Observation.objects.filter(source=source, external_key=OuterRef("external_key"))
        .values("external_key")
        .annotate(latest_id=Max("id"))
        .values("latest_id")[:1]
    )
    latest = Observation.objects.filter(source=source, id=Subquery(latest_ids))
    now = timezone.now()

    if tier == "hot":
        hot_offers = ClickSignal.objects.filter(
            count_7d__gt=0,
            updated_at__gte=now - timedelta(days=7),
        ).values("offer_uid")
        return list(latest.filter(offer_uid__in=hot_offers).values_list("url", flat=True))
    if tier == "warm":
        changed_keys = (
            Observation.objects.filter(source=source, observed_at__gte=now - timedelta(days=7))
            .values("external_key")
            .annotate(changes=Count("raw_price_text", distinct=True))
            .filter(changes__gt=1)
            .values("external_key")
        )
        return list(
            latest.filter(
                external_key__in=changed_keys, last_seen_at__gte=now - timedelta(days=30)
            ).values_list("url", flat=True)
        )
    if tier == "dormant":
        return list(
            latest.filter(last_seen_at__lt=now - timedelta(days=30)).values_list("url", flat=True)
        )

    excluded = set(urls_for_tier(source, "hot")) | set(urls_for_tier(source, "warm"))
    return list(
        latest.filter(last_seen_at__gte=now - timedelta(days=30))
        .exclude(url__in=excluded)
        .values_list("url", flat=True)
    )


def get_cursor_position(source: Source, tier: str) -> int:
    cursor, _ = CrawlCursor.objects.get_or_create(source=source, tier=tier)
    try:
        return int(cursor.position)
    except ValueError:
        return 0


def set_cursor_position(source: Source, tier: str, position: int) -> None:
    CrawlCursor.objects.update_or_create(
        source=source, tier=tier, defaults={"position": str(position)}
    )
