import logging
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    allowed: bool
    robots_url: str


class RobotsPolicy:
    def __init__(
        self,
        redis: Redis,
        client: httpx.AsyncClient,
        user_agent: str,
        cache_seconds: int = 86400,
    ) -> None:
        self.redis = redis
        self.client = client
        self.user_agent = user_agent
        self.cache_seconds = cache_seconds

    async def allowed(self, url: str) -> RobotsDecision:
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        robots_url = f"{origin}/robots.txt"
        cache_key = f"crawler:robots:{origin}"
        cached = await self.redis.get(cache_key)
        if cached is None:
            body = await self._fetch(robots_url)
            await self.redis.set(cache_key, body, ex=self.cache_seconds)
        else:
            body = cached.decode("utf-8", errors="replace") if isinstance(cached, bytes) else cached

        if body == "__DENY__":
            return RobotsDecision(False, robots_url)

        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(body.splitlines())
        return RobotsDecision(parser.can_fetch(self.user_agent, url), robots_url)

    async def _fetch(self, robots_url: str) -> str:
        try:
            response = await self.client.get(robots_url)
        except httpx.HTTPError:
            logger.exception("robots_fetch_failed", extra={"topic": robots_url})
            return "__DENY__"
        if response.status_code == 404:
            return "User-agent: *\nDisallow:"
        if not 200 <= response.status_code < 300:
            logger.warning(
                "robots_fetch_non_success",
                extra={"topic": robots_url, "event_id": str(response.status_code)},
            )
            return "__DENY__"
        return response.text
