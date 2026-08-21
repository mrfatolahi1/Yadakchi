from datetime import UTC, datetime

import pytest

from crawler.archive import ArchiveService
from crawler.crawl import CrawlerRunner
from crawler.fetcher import FetchResult
from crawler.models import ArchivedDocument, CrawlCursor, Observation, Source
from tests.conftest import RecordingPublisher
from tests.test_archive import MemoryObjectStore


def listing_page(url: str) -> bytes:
    slug = url.rsplit("/", 1)[-1]
    return f"""
    <html><body><div class="maxshop-products">
      <div class="product-box">
        <div class="product-box-title">
          <a class="product-link" href="{url}"><h3>raw title {slug}</h3></a>
        </div>
        <img class="product-image" src="/{slug}.jpg">
      </div>
      <div class="product-box-price"><span class="product-price">raw price {slug}</span></div>
    </div></body></html>
    """.encode()


class InterruptingFetcher:
    def __init__(self, fail_url: str | None = None) -> None:
        self.fail_url = fail_url
        self.requested: list[str] = []

    async def fetch(self, source: Source, url: str) -> FetchResult:
        del source
        self.requested.append(url)
        if url == self.fail_url:
            raise RuntimeError("worker interrupted")
        return FetchResult(url, 200, listing_page(url), {})


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_interrupted_crawl_resumes_from_persisted_cursor(
    source: Source, publisher: RecordingPublisher
) -> None:
    urls = ["https://seller.example/p/one", "https://seller.example/p/two"]
    archive = ArchiveService(MemoryObjectStore())
    first_fetcher = InterruptingFetcher(fail_url=urls[1])
    runner = CrawlerRunner(first_fetcher, archive, publisher)

    with pytest.raises(RuntimeError, match="interrupted"):
        await runner.run(
            source,
            "discovery",
            urls=urls,
            measured_at=datetime(2026, 8, 21, 10, tzinfo=UTC),
        )

    cursor = await CrawlCursor.objects.aget(source=source, tier="discovery")
    assert cursor.position == "1"
    assert first_fetcher.requested == urls

    resumed_fetcher = InterruptingFetcher()
    resumed = await CrawlerRunner(
        resumed_fetcher,
        archive,
        publisher,
    ).run(
        source,
        "discovery",
        urls=urls,
        measured_at=datetime(2026, 8, 21, 10, 5, tzinfo=UTC),
    )

    assert resumed_fetcher.requested == [urls[1]]
    assert resumed.new_observations == 1
    cursor = await CrawlCursor.objects.aget(source=source, tier="discovery")
    assert cursor.position == "0"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_running_same_crawl_twice_creates_one_archive_and_one_event(
    source: Source, publisher: RecordingPublisher
) -> None:
    urls = ["https://seller.example/p/one"]
    store = MemoryObjectStore()
    archive = ArchiveService(store)
    measured_at = datetime(2026, 8, 21, 10, tzinfo=UTC)

    first = await CrawlerRunner(InterruptingFetcher(), archive, publisher).run(
        source, "discovery", urls=urls, measured_at=measured_at
    )
    second = await CrawlerRunner(InterruptingFetcher(), archive, publisher).run(
        source, "discovery", urls=urls, measured_at=measured_at
    )

    assert first.new_observations == 1
    assert second.new_observations == 0
    assert store.put_count == 1
    assert await ArchivedDocument.objects.acount() == 1
    assert await Observation.objects.acount() == 1
    assert len(publisher.messages) == 1
