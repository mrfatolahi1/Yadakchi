"""Prometheus metrics. Names are fixed by the spec; Grafana panels use them.

Scraped by the platform's Prometheus at `ai:8000/metrics` (see
platform/observability/prometheus.yml), so the collectors live on the default
registry and are module-level singletons: creating a second app in the same
process must not re-register them.
"""

from __future__ import annotations

from prometheus_client import (
    REGISTRY as DEFAULT_REGISTRY,
)
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

#: op: extract | judge | embed. backend: stub | local | domestic | external.
#: status: ok | error | budget_exhausted | invalid.
CALLS = Counter(
    "yadakchi_ai_calls_total",
    "Model operations served, by operation, backend and outcome.",
    ["op", "backend", "status"],
)

DURATION = Histogram(
    "yadakchi_ai_duration_seconds",
    "Wall-clock duration of a model operation, end to end.",
    ["op"],
    buckets=(0.005, 0.025, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

CACHE_HITS = Counter(
    "yadakchi_ai_cache_hits_total",
    "Operations answered from cache without reaching the model.",
    ["op"],
)

CACHE_MISSES = Counter(
    "yadakchi_ai_cache_misses_total",
    "Operations that had to reach the model.",
    ["op"],
)

CACHE_HIT_RATIO = Gauge(
    "yadakchi_ai_cache_hit_ratio",
    "Cache hits divided by lookups since process start, by operation.",
    ["op"],
)

TOKENS = Counter(
    "yadakchi_ai_tokens_total",
    "Tokens exchanged with the provider.",
    ["direction"],  # prompt | completion
)

BUDGET_RATIO = Gauge(
    "yadakchi_ai_budget_used_ratio",
    "Today's spend divided by AI_DAILY_BUDGET, clamped to [0, 1]. 1.0 means "
    "every further call is refused with HTTP 429.",
)

#: One increment per actual model invocation — the number a cache is supposed
#: to keep down. A repair retry counts as a second invocation, on purpose.
MODEL_INVOCATIONS = Counter(
    "yadakchi_ai_model_invocations_total",
    "Times the model was actually invoked, cache misses only.",
    ["op", "backend"],
)

UPSTREAM_ATTEMPTS = Counter(
    "yadakchi_ai_upstream_attempts_total",
    "HTTP attempts against the model provider, including retries.",
    ["op", "outcome"],  # ok | retry | failed
)

BUDGET_RATIO.set(0.0)

#: Counters are write-only in prometheus_client, so the ratio gauge keeps its
#: own tally. Same numbers, no reaching into private attributes.
_LOOKUPS: dict[str, list[int]] = {}


def render(registry: CollectorRegistry = DEFAULT_REGISTRY) -> bytes:
    return generate_latest(registry)


def record_cache(op: str, *, hit: bool) -> None:
    """Count one cache lookup and refresh the hit-ratio gauge for `op`."""
    tally = _LOOKUPS.setdefault(op, [0, 0])
    if hit:
        CACHE_HITS.labels(op=op).inc()
        tally[0] += 1
    else:
        CACHE_MISSES.labels(op=op).inc()
        tally[1] += 1
    total = tally[0] + tally[1]
    CACHE_HIT_RATIO.labels(op=op).set(tally[0] / total if total else 0.0)
