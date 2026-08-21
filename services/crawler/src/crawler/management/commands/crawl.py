import asyncio
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from crawler.crawl import CrawlerRunner
from crawler.models import Source
from crawler.producer import build_kafka_publisher
from crawler.runtime import build_archive_service, build_fetcher


class Command(BaseCommand):
    help = "Crawl one source and tier"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--tier", default="discovery")
        parser.add_argument("--limit", type=int)

    def handle(self, *args: Any, **options: Any) -> None:
        source = Source.objects.get(key=options["source"], is_active=True)

        async def execute() -> None:
            fetcher, client, redis = await build_fetcher()
            try:
                result = await CrawlerRunner(
                    fetcher, build_archive_service(), build_kafka_publisher()
                ).run(source, options["tier"], limit=options["limit"])
            finally:
                await client.aclose()
                await redis.aclose()
            self.stdout.write(
                self.style.SUCCESS(
                    f"attempted={result.attempted} parsed_ok={result.parsed_ok} "
                    f"new_observations={result.new_observations}"
                )
            )

        asyncio.run(execute())
