"""Prometheus metrics, exposed at ``GET /metrics``."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

EVENTS_CONSUMED = Counter(
    "catalog_events_consumed_total", "Events applied to local state", ["topic"]
)
EVENTS_SKIPPED = Counter(
    "catalog_events_skipped_total",
    "Events deliberately not applied: duplicate delivery or stale facts",
    ["topic"],
)
EVENTS_FAILED = Counter(
    "catalog_events_failed_total", "Events routed to the DLQ", ["topic", "reason"]
)
PRODUCTS_EMITTED = Counter(
    "catalog_products_changed_emitted_total", "products.changed events published"
)
SELLERS_EMITTED = Counter(
    "catalog_sellers_changed_emitted_total", "sellers.changed events published"
)
PRODUCTS_PUBLISHED = Gauge(
    "catalog_products_published", "Products currently passing the publication gate"
)
API_LATENCY = Histogram("catalog_api_request_seconds", "Read API latency", ["endpoint"])
