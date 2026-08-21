from prometheus_client import Counter

ROBOTS_SKIPS = Counter(
    "yadakchi_crawler_robots_skips_total", "URLs skipped by robots.txt", ("source",)
)
FETCH_ERRORS = Counter(
    "yadakchi_crawler_fetch_errors_total", "Seller fetch failures", ("source", "reason")
)
ARCHIVE_DEDUPLICATIONS = Counter(
    "yadakchi_crawler_archive_deduplications_total",
    "Archive writes avoided by content deduplication",
    ("source",),
)
ADAPTER_HEALTH_ALERTS = Counter(
    "yadakchi_adapter_health_alerts_total", "Broken adapter alarms", ("source",)
)
