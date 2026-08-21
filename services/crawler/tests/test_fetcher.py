import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import fakeredis.aioredis
import httpx
import pytest

from crawler.fetcher import AsyncFetcher, RedisPolitenessGate
from crawler.metrics import ROBOTS_SKIPS
from crawler.models import Source
from crawler.robots import RobotsDecision


class AllowRobots:
    async def allowed(self, url: str) -> RobotsDecision:
        return RobotsDecision(True, f"{url}/robots.txt")


class DenyRobots:
    async def allowed(self, url: str) -> RobotsDecision:
        return RobotsDecision(False, f"{url}/robots.txt")


class NoWaitGate:
    async def wait(self, source_key: str, delay_ms: int) -> None:
        del source_key, delay_ms


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_robots_disallowed_url_is_never_fetched_and_counter_increments(
    source: Source,
) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=b"should not be fetched", request=request)

    before = ROBOTS_SKIPS.labels(source=source.key)._value.get()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = AsyncFetcher(client, DenyRobots(), NoWaitGate())  # type: ignore[arg-type]
        result = await fetcher.fetch(source, "https://seller.example/private")

    assert result is None
    assert requests == 0
    assert ROBOTS_SKIPS.labels(source=source.key)._value.get() == before + 1


class RecordingHandler(BaseHTTPRequestHandler):
    request_times: list[float] = []

    def do_GET(self) -> None:
        self.__class__.request_times.append(time.monotonic())
        body = b"<html><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_concurrent_workers_share_politeness_delay_against_local_server(
    source: Source,
) -> None:
    source.politeness_delay_ms = 180
    server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    RecordingHandler.request_times = []
    redis = fakeredis.aioredis.FakeRedis()
    gate_one = RedisPolitenessGate(redis)
    gate_two = RedisPolitenessGate(redis)
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        async with (
            httpx.AsyncClient(trust_env=False) as client_one,
            httpx.AsyncClient(trust_env=False) as client_two,
        ):
            first = AsyncFetcher(client_one, AllowRobots(), gate_one)  # type: ignore[arg-type]
            second = AsyncFetcher(client_two, AllowRobots(), gate_two)  # type: ignore[arg-type]
            await asyncio.gather(
                first.fetch(source, f"{base_url}/one"),
                second.fetch(source, f"{base_url}/two"),
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        await redis.aclose()

    assert len(RecordingHandler.request_times) == 2
    spacing = abs(RecordingHandler.request_times[1] - RecordingHandler.request_times[0])
    assert spacing >= 0.15
