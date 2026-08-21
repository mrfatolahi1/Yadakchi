from datetime import UTC, datetime, timedelta

import pytest

from crawler.adapters.base import ListingStub
from crawler.archive import ArchiveService
from crawler.models import Observation, OutboxEvent, Source
from crawler.observations import cap_fragment, observe_listing
from crawler.producer import LISTINGS_TOPIC, flush_outbox
from tests.conftest import RecordingPublisher
from tests.test_archive import MemoryObjectStore


@pytest.mark.django_db
def test_duplicate_crawl_emits_exactly_one_schema_valid_listing_event(
    source: Source, publisher: RecordingPublisher
) -> None:
    observed_at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    archive = (
        ArchiveService(MemoryObjectStore())
        .archive(source, "https://seller.example/page", b"raw page", 200, observed_at)
        .document
    )
    stub = ListingStub(
        external_key="sku-1",
        url="https://seller.example/p/sku-1",
        raw_title="عنوان خام",
        raw_price_text="۲۳,۰۰۰ تومان",
        raw_stock_text="موجود",
        image_url="https://seller.example/1.jpg",
        raw_fragment='<article data-id="sku-1">عنوان خام</article>',
    )

    first = observe_listing(source, archive, stub, observed_at)
    seen_again_at = observed_at + timedelta(hours=1)
    second = observe_listing(source, archive, stub, seen_again_at)
    sent = flush_outbox(publisher)

    assert first.created is True
    assert second.created is False
    assert first.observation.pk == second.observation.pk
    first.observation.refresh_from_db()
    assert first.observation.last_seen_at == seen_again_at
    assert Observation.objects.count() == 1
    assert OutboxEvent.objects.count() == 1
    assert sent == 1
    assert len(publisher.messages) == 1
    topic, key, event = publisher.messages[0]
    assert topic == LISTINGS_TOPIC
    assert key == "test-source:sku-1"
    assert event["payload"]["raw_title"] == "عنوان خام"
    assert event["payload"]["archive_uri"] == archive.archive_uri


def test_fragment_cap_is_byte_based_and_keeps_valid_utf8() -> None:
    capped = cap_fragment("ی" * 40000, max_bytes=65536)

    assert len(capped.encode("utf-8")) <= 65536
    assert capped
