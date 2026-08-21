import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from asgiref.sync import sync_to_async
from django.utils import timezone

from crawler.adapters import get_adapter
from crawler.archive import ArchiveService
from crawler.fetcher import FetchResult
from crawler.health import record_adapter_health
from crawler.models import Source
from crawler.observations import observe_listing
from crawler.producer import EventPublisher, flush_outbox
from crawler.scheduler import get_cursor_position, set_cursor_position, urls_for_tier

logger = logging.getLogger(__name__)


class PageFetcher(Protocol):
    async def fetch(self, source: Source, url: str) -> FetchResult | None: ...


@dataclass(frozen=True, slots=True)
class CrawlRunResult:
    attempted: int
    parsed_ok: int
    new_observations: int


class CrawlerRunner:
    def __init__(
        self,
        fetcher: PageFetcher,
        archive: ArchiveService,
        publisher: EventPublisher,
    ) -> None:
        self.fetcher = fetcher
        self.archive = archive
        self.publisher = publisher

    async def run(
        self,
        source: Source,
        tier: str,
        urls: Sequence[str] | None = None,
        measured_at: datetime | None = None,
        limit: int | None = None,
    ) -> CrawlRunResult:
        adapter = get_adapter(source.adapter_key)
        selected = (
            list(urls) if urls is not None else await sync_to_async(urls_for_tier)(source, tier)
        )
        start = await sync_to_async(get_cursor_position)(source, tier)
        selected = selected[start:]
        if limit is not None:
            selected = selected[:limit]

        attempted = 0
        parsed_ok = 0
        new_observations = 0
        for offset, url in enumerate(selected, start=start):
            fetched = await self.fetcher.fetch(source, url)
            if fetched is None:
                await sync_to_async(set_cursor_position)(source, tier, offset + 1)
                continue
            attempted += 1
            observed_at = measured_at or timezone.now()
            archived = await sync_to_async(self.archive.archive)(
                source,
                fetched.url,
                fetched.body,
                fetched.status_code,
                observed_at,
            )
            listings = adapter.extract_listings(fetched.body, fetched.url)
            if listings:
                parsed_ok += 1
            for listing in listings:
                result = await sync_to_async(observe_listing)(
                    source, archived.document, listing, observed_at
                )
                new_observations += int(result.created)
            await sync_to_async(flush_outbox)(self.publisher)
            await sync_to_async(set_cursor_position)(source, tier, offset + 1)
            logger.info(
                "crawl_page_complete",
                extra={"source": source.key, "topic": fetched.url},
            )

        await sync_to_async(record_adapter_health)(
            source, attempted, parsed_ok, measured_at or timezone.now()
        )
        await sync_to_async(flush_outbox)(self.publisher)
        await sync_to_async(set_cursor_position)(source, tier, 0)
        return CrawlRunResult(attempted, parsed_ok, new_observations)
