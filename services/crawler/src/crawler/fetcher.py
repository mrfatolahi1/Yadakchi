import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx
from redis.asyncio import Redis
from redis.exceptions import WatchError

from crawler.metrics import FETCH_ERRORS, ROBOTS_SKIPS
from crawler.models import Source
from crawler.robots import RobotsPolicy

logger = logging.getLogger(__name__)


class PolitenessGate(Protocol):
    async def wait(self, source_key: str, delay_ms: int) -> None: ...


class RedisPolitenessGate:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def wait(self, source_key: str, delay_ms: int) -> None:
        if delay_ms <= 0:
            return
        key = f"crawler:politeness:{source_key}"
        while True:
            pipe = self.redis.pipeline()
            try:
                await pipe.watch(key)
                seconds, microseconds = await self.redis.time()
                now_ms = seconds * 1000 + microseconds // 1000
                raw_next = await pipe.get(key)
                current_next = int(raw_next) if raw_next is not None else now_ms
                reserved_at = max(now_ms, current_next)
                wait_ms = max(0, reserved_at - now_ms)
                pipe.multi()  # type: ignore[no-untyped-call]
                pipe.set(key, reserved_at + delay_ms, px=max(86400000, delay_ms * 10))
                await pipe.execute()
                if wait_ms:
                    await asyncio.sleep(wait_ms / 1000)
                return
            except WatchError:
                continue
            finally:
                await pipe.reset()  # type: ignore[no-untyped-call]


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status_code: int
    body: bytes
    headers: dict[str, str]


class AsyncFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        robots: RobotsPolicy,
        gate: PolitenessGate,
    ) -> None:
        self.client = client
        self.robots = robots
        self.gate = gate

    async def fetch(self, source: Source, url: str) -> FetchResult | None:
        decision = await self.robots.allowed(url)
        if not decision.allowed:
            ROBOTS_SKIPS.labels(source=source.key).inc()
            logger.info("robots_disallowed", extra={"source": source.key, "topic": url})
            return None

        await self.gate.wait(source.key, source.politeness_delay_ms)
        try:
            response = await self.client.get(url)
        except httpx.TimeoutException:
            FETCH_ERRORS.labels(source=source.key, reason="timeout").inc()
            logger.exception("fetch_timeout", extra={"source": source.key, "topic": url})
            raise
        except httpx.HTTPError:
            FETCH_ERRORS.labels(source=source.key, reason="http_error").inc()
            logger.exception("fetch_failed", extra={"source": source.key, "topic": url})
            raise
        return FetchResult(
            url=str(response.url),
            status_code=response.status_code,
            body=response.content,
            headers={key: value for key, value in response.headers.items()},
        )


def build_http_client(
    user_agent: str, timeout_seconds: float, proxy_url: str | None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        http2=True,
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_seconds),
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, br"},
        proxy=proxy_url,
        trust_env=False,
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
    )
