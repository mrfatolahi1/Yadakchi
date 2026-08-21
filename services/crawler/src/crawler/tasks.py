import asyncio
import logging

from celery import shared_task

from crawler.crawl import CrawlerRunner
from crawler.models import Source
from crawler.producer import build_kafka_publisher
from crawler.runtime import build_archive_service, build_fetcher

logger = logging.getLogger(__name__)


@shared_task(name="crawler.tasks.dispatch_tier")  # type: ignore[misc]
def dispatch_tier(tier: str) -> int:
    source_keys = list(Source.objects.filter(is_active=True).values_list("key", flat=True))
    for source_key in source_keys:
        crawl_source.delay(source_key, tier)
    return len(source_keys)


@shared_task(name="crawler.tasks.crawl_source")  # type: ignore[misc]
def crawl_source(source_key: str, tier: str = "discovery") -> dict[str, int]:
    source = Source.objects.get(key=source_key, is_active=True)

    async def execute() -> dict[str, int]:
        fetcher, client, redis = await build_fetcher()
        try:
            result = await CrawlerRunner(
                fetcher, build_archive_service(), build_kafka_publisher()
            ).run(source, tier)
        finally:
            await client.aclose()
            await redis.aclose()
        return {
            "attempted": result.attempted,
            "parsed_ok": result.parsed_ok,
            "new_observations": result.new_observations,
        }

    result = asyncio.run(execute())
    logger.info("crawl_run_complete", extra={"source": source_key, "topic": tier})
    return result
