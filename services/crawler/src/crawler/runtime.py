import httpx
from django.conf import settings
from redis.asyncio import Redis

from crawler.archive import ArchiveService, S3ObjectStore
from crawler.fetcher import AsyncFetcher, RedisPolitenessGate, build_http_client
from crawler.robots import RobotsPolicy


def build_archive_service() -> ArchiveService:
    store = S3ObjectStore(
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        bucket=settings.MINIO_BUCKET,
        region=settings.MINIO_REGION,
    )
    store.ensure_bucket()
    return ArchiveService(store)


async def build_fetcher() -> tuple[AsyncFetcher, httpx.AsyncClient, Redis]:
    redis = Redis.from_url(settings.REDIS_URL)
    client = build_http_client(
        settings.CRAWLER_USER_AGENT,
        settings.CRAWLER_HTTP_TIMEOUT_SECONDS,
        settings.CRAWLER_PROXY_URL,
    )
    robots = RobotsPolicy(redis, client, settings.CRAWLER_USER_AGENT)
    fetcher = AsyncFetcher(client, robots, RedisPolitenessGate(redis))
    return fetcher, client, redis
