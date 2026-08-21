"""The cache. Not optional.

Duplicate titles are the norm in this data set, not the exception: the same
«لنت ترمز جلو پراید اصلی» is listed by dozens of sellers, and `crawler` replays
its whole archive whenever an algorithm changes. A repeated title that reaches
the model twice costs real money on `domestic`/`external` and real minutes of a
CPU-only host on `local`, so the cache is part of the design, not a speedup.

Two layers:

* an in-process LRU, which absorbs the burst of identical titles inside one
  batch without a round trip;
* Redis (db 8) with a 30-day TTL, which is what makes the cache survive a
  restart and be shared by every worker.

Redis being down degrades the cache to the LRU and is logged; it never fails a
request. A cache that can take the service down is worse than no cache.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any, Final, Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError

from ai.config import Settings
from ai.logging_ import get_logger

logger = get_logger(__name__)

KEY_PREFIX: Final[str] = "ai:cache"
_SEPARATOR: Final[bytes] = b"\x1f"


def cache_key(
    *,
    backend: str,
    model: str,
    prompt_version: str,
    operation: str,
    payload: str,
) -> str:
    """sha256(backend + model + prompt version + operation + input).

    Every component matters: the same title asked of a different model, or
    under a different prompt version, is a different question. Bumping
    PROMPT_VERSION invalidates the whole cache by construction — no flush.
    """
    digest = hashlib.sha256()
    for part in (backend, model, prompt_version, operation, payload):
        digest.update(part.encode("utf-8"))
        digest.update(_SEPARATOR)
    return digest.hexdigest()


def canonical(payload: Any) -> str:
    """Stable text for a structured input: key order must not change the key."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class Cache:
    """LRU in front of Redis. Values are JSON documents."""

    def __init__(self, settings: Settings, *, redis: Redis | None = None) -> None:
        self._settings = settings
        self._ttl = settings.ai_cache_ttl_seconds
        self._enabled = settings.ai_cache_enabled
        self._max_entries = settings.ai_cache_lru_size
        self._lru: OrderedDict[str, str] = OrderedDict()
        self._redis: Redis | None = redis
        self._redis_configured = redis is not None or bool(settings.redis_url)
        self._redis_ok = redis is not None
        self._warned = False

    # ------------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        """Open Redis if configured. A failure here is a warning, not a crash."""
        if not self._enabled or self._redis is not None:
            if self._redis is not None:
                await self._ping()
            return
        if not self._settings.redis_url:
            logger.warning(
                "REDIS_URL is not set — the cache is in-process only and will not "
                "survive a restart or be shared between workers"
            )
            return
        self._redis = Redis.from_url(
            self._settings.redis_url,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
        await self._ping()

    async def _ping(self) -> bool:
        if self._redis is None:
            return False
        try:
            await self._redis.ping()
        except (RedisError, OSError) as exc:
            self._degrade(exc)
            return False
        if not self._redis_ok:
            logger.info("redis cache connected", extra={"db": "8"})
        self._redis_ok = True
        return True

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            self._redis_ok = False

    # ---------------------------------------------------------------- status
    @property
    def redis_status(self) -> Literal["up", "down", "disabled"]:
        if not self._enabled:
            return "disabled"
        if not self._redis_configured:
            return "disabled"
        return "up" if self._redis_ok else "down"

    @property
    def entries(self) -> int:
        return len(self._lru)

    def _degrade(self, exc: Exception) -> None:
        self._redis_ok = False
        if not self._warned:
            self._warned = True
            logger.warning(
                "redis cache unavailable — falling back to the in-process LRU",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )

    # ------------------------------------------------------------ operations
    async def get(self, key: str) -> dict[str, Any] | None:
        """Look up one key. Returns None on a miss or on any cache failure."""
        if not self._enabled:
            return None
        cached = self._lru.get(key)
        if cached is not None:
            self._lru.move_to_end(key)
            return self._decode(cached)

        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(f"{KEY_PREFIX}:{key}")
        except (RedisError, OSError) as exc:
            self._degrade(exc)
            return None
        self._redis_ok = True
        if raw is None:
            return None
        self._remember(key, raw)
        return self._decode(raw)

    async def set(self, key: str, value: dict[str, Any]) -> None:
        """Store one key in both layers. Failures are logged, never raised."""
        if not self._enabled:
            return
        raw = json.dumps(value, ensure_ascii=False)
        self._remember(key, raw)
        if self._redis is None:
            return
        try:
            await self._redis.set(f"{KEY_PREFIX}:{key}", raw, ex=self._ttl)
        except (RedisError, OSError) as exc:
            self._degrade(exc)
            return
        self._redis_ok = True

    def _remember(self, key: str, raw: str) -> None:
        self._lru[key] = raw
        self._lru.move_to_end(key)
        while len(self._lru) > self._max_entries:
            self._lru.popitem(last=False)

    @staticmethod
    def _decode(raw: str) -> dict[str, Any] | None:
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    @property
    def redis(self) -> Redis | None:
        """Shared connection, so the budget guard needs no second client."""
        return self._redis if self._redis_ok else None
