from datetime import UTC, datetime

import pytest

from crawler.adapters.base import ListingStub
from crawler.archive import ArchiveService
from crawler.models import Source
from crawler.observations import observe_listing
from crawler.producer import LISTINGS_TOPIC
from crawler.replay import replay_observations
from tests.conftest import RecordingPublisher
from tests.test_archive import MemoryObjectStore


@pytest.mark.django_db
def test_replay_reemits_original_values_with_zero_seller_requests(
    source: Source, publisher: RecordingPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    def outbound_request_forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("replay attempted an outbound seller request")

    monkeypatch.setattr("httpx.get", outbound_request_forbidden)
    observed_at = datetime(2026, 8, 20, 12, tzinfo=UTC)
    archive = (
        ArchiveService(MemoryObjectStore())
        .archive(source, "https://seller.example/page", b"full raw page", 200, observed_at)
        .document
    )
    original = observe_listing(
        source,
        archive,
        ListingStub(
            external_key="sku-replay",
            url="https://seller.example/p/sku-replay",
            raw_title="عنوان بدون تغییر",
            raw_price_text="قیمت با تماس",
            raw_stock_text=None,
            image_url=None,
            raw_fragment="<article>اصل صفحه</article>",
        ),
        observed_at,
    ).observation

    emitted = replay_observations(source, publisher, rate_per_second=0)

    assert emitted == 1
    topic, key, event = publisher.messages[0]
    assert topic == LISTINGS_TOPIC
    assert key == "test-source:sku-replay"
    assert event["payload"]["observed_at"] == "2026-08-20T12:00:00Z"
    assert event["payload"]["fragment_hash"] == original.fragment_hash
    assert event["payload"]["raw_fragment"] == original.raw_fragment


@pytest.mark.django_db
def test_replay_cursor_is_resumable(source: Source, publisher: RecordingPublisher) -> None:
    observed_at = datetime(2026, 8, 20, 12, tzinfo=UTC)
    archive = (
        ArchiveService(MemoryObjectStore())
        .archive(source, "https://seller.example/page", b"full raw page", 200, observed_at)
        .document
    )
    observe_listing(
        source,
        archive,
        ListingStub("sku", "https://seller.example/p/sku", "raw", None, None, None, "<p>x</p>"),
        observed_at,
    )

    assert replay_observations(source, publisher, rate_per_second=0) == 1
    assert replay_observations(source, publisher, rate_per_second=0) == 0
    assert replay_observations(source, publisher, rate_per_second=0, reset=True) == 1
