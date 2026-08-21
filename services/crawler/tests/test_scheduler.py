from datetime import UTC, datetime, timedelta

import pytest

from crawler.adapters.base import ListingStub
from crawler.archive import ArchiveService
from crawler.models import ClickSignal, Source
from crawler.observations import observe_listing
from crawler.scheduler import urls_for_tier
from tests.test_archive import MemoryObjectStore


@pytest.mark.django_db
def test_hot_tier_excludes_expired_click_signal(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 8, 21, 12, tzinfo=UTC)
    monkeypatch.setattr("crawler.scheduler.timezone.now", lambda: now)
    archive = (
        ArchiveService(MemoryObjectStore())
        .archive(source, "https://seller.example/page", b"raw", 200, now)
        .document
    )
    observation = observe_listing(
        source,
        archive,
        ListingStub(
            external_key="sku-hot",
            url="https://seller.example/p/sku-hot",
            raw_title="raw",
            raw_price_text=None,
            raw_stock_text=None,
            image_url=None,
            raw_fragment="<article>raw</article>",
        ),
        now,
    ).observation
    signal = ClickSignal.objects.create(
        offer_uid=observation.offer_uid,
        count_7d=3,
        updated_at=now - timedelta(days=8),
    )

    assert urls_for_tier(source, "hot") == []

    signal.updated_at = now - timedelta(days=1)
    signal.save(update_fields=("updated_at",))
    assert urls_for_tier(source, "hot") == [observation.url]
