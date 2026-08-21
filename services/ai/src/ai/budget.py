"""The daily budget guard.

The failure this prevents is not a large bill. It is a service that quietly
gets worse: a provider starts refusing calls halfway through a reprocess, the
extraction silently returns nulls, and three weeks later someone notices that
every offer crawled in August has no brand.

So the guard is loud. At 100% of `AI_DAILY_BUDGET` every model call is refused
with **HTTP 429 and the code `budget_exhausted`**, which is the exact signal
`enricher` is built to catch and fall back to its rules-only path on. At 80% we
log a warning once, so somebody can act before that happens.

What is counted depends on what is scarce:

* a metered provider is billed per token, so we count money using
  `AI_COST_PER_1K_INPUT` / `AI_COST_PER_1K_OUTPUT`;
* a model on the host costs the host's time, so we count wall-clock seconds.

Usage lives in Redis when it is available, so every worker shares one counter
and a restart does not reset the day. Without Redis it is per-process — still
a guard, just a coarser one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final

from redis.exceptions import RedisError

from ai.cache import Cache
from ai.config import Settings
from ai.errors import BudgetExhaustedError
from ai.logging_ import get_logger
from ai.metrics import BUDGET_RATIO

logger = get_logger(__name__)

KEY_PREFIX: Final[str] = "ai:budget"
#: Two days, so yesterday's number is still readable while today runs.
KEY_TTL_SECONDS: Final[int] = 2 * 24 * 3600
WARN_AT: Final[float] = 0.8


@dataclass(frozen=True)
class BudgetSnapshot:
    """What `/health` reports."""

    day: str
    used: float
    limit: float
    ratio: float
    unit: str
    enabled: bool
    exhausted: bool


def today() -> str:
    return datetime.now(tz=UTC).date().isoformat()


class BudgetGuard:
    """Track today's spend and refuse to exceed it."""

    def __init__(self, settings: Settings, cache: Cache) -> None:
        self._settings = settings
        self._cache = cache
        self._local: dict[str, float] = {}
        self._warned_day: str | None = None

    # ------------------------------------------------------------ accounting
    def cost_of(self, *, prompt_tokens: int, completion_tokens: int, seconds: float) -> float:
        """Convert one call into budget units."""
        if self._settings.budget_unit == "currency":
            return (prompt_tokens / 1000) * self._settings.ai_cost_per_1k_input + (
                completion_tokens / 1000
            ) * self._settings.ai_cost_per_1k_output
        return seconds

    async def used(self) -> float:
        day = today()
        redis = self._cache.redis
        if redis is not None:
            try:
                raw = await redis.get(f"{KEY_PREFIX}:{day}")
            except (RedisError, OSError) as exc:
                logger.warning(
                    "budget counter unreadable in redis — using the local counter",
                    extra={"error": f"{type(exc).__name__}: {exc}"},
                )
            else:
                if raw is not None:
                    return float(raw)
                return 0.0
        return self._local.get(day, 0.0)

    async def record(self, amount: float) -> float:
        """Add one call's cost to today. Returns the new total."""
        if amount <= 0:
            return await self._refresh_gauge()
        day = today()
        redis = self._cache.redis
        total: float | None = None
        if redis is not None:
            try:
                total = float(await redis.incrbyfloat(f"{KEY_PREFIX}:{day}", amount))
                await redis.expire(f"{KEY_PREFIX}:{day}", KEY_TTL_SECONDS)
            except (RedisError, OSError) as exc:
                logger.warning(
                    "budget counter not written to redis — using the local counter",
                    extra={"error": f"{type(exc).__name__}: {exc}"},
                )
                total = None
        if total is None:
            total = self._local.get(day, 0.0) + amount
            self._local[day] = total
            for stale in [key for key in self._local if key != day]:
                del self._local[stale]

        self._publish(total)
        self._maybe_warn(day, total)
        return total

    # --------------------------------------------------------------- guarding
    async def check(self) -> None:
        """Raise `BudgetExhaustedError` when today's budget is spent.

        Called *after* the cache lookup: an answer we already have costs
        nothing, and refusing to serve it would be pure loss.
        """
        if not self._settings.ai_budget_enabled:
            return
        used = await self.used()
        limit = self._settings.ai_daily_budget
        self._publish(used)
        if used < limit:
            self._maybe_warn(today(), used)
            return
        raise BudgetExhaustedError(
            f"today's AI budget of {limit:g} {self._settings.budget_unit} is spent; "
            "callers should fall back to their rules-only path until UTC midnight",
            {
                "code_hint": "budget_exhausted",
                "used": round(used, 6),
                "limit": limit,
                "unit": self._settings.budget_unit,
                "day": today(),
                "resets_at": self.resets_at().isoformat(),
            },
        )

    def resets_at(self) -> datetime:
        now = datetime.now(tz=UTC)
        tomorrow = date.fromordinal(now.date().toordinal() + 1)
        return datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC)

    def seconds_until_reset(self) -> int:
        return max(1, int((self.resets_at() - datetime.now(tz=UTC)).total_seconds()))

    async def snapshot(self) -> BudgetSnapshot:
        used = await self.used()
        limit = self._settings.ai_daily_budget
        ratio = self._ratio(used)
        self._publish(used)
        return BudgetSnapshot(
            day=today(),
            used=round(used, 6),
            limit=limit,
            ratio=round(ratio, 6),
            unit=self._settings.budget_unit,
            enabled=self._settings.ai_budget_enabled,
            exhausted=self._settings.ai_budget_enabled and used >= limit,
        )

    # ---------------------------------------------------------------- internals
    def _ratio(self, used: float) -> float:
        limit = self._settings.ai_daily_budget
        if limit <= 0:
            return 1.0
        return min(used / limit, 1.0)

    def _publish(self, used: float) -> None:
        """The gauge is a fill level: clamped to 1.0, where 1.0 means refusing."""
        BUDGET_RATIO.set(self._ratio(used))

    async def _refresh_gauge(self) -> float:
        used = await self.used()
        self._publish(used)
        return used

    def _maybe_warn(self, day: str, used: float) -> None:
        if not self._settings.ai_budget_enabled or self._warned_day == day:
            return
        if self._ratio(used) < WARN_AT:
            return
        self._warned_day = day
        logger.warning(
            "AI daily budget is %.0f%% spent",
            self._ratio(used) * 100,
            extra={
                "used": round(used, 6),
                "limit": self._settings.ai_daily_budget,
                "unit": self._settings.budget_unit,
                "day": day,
            },
        )
