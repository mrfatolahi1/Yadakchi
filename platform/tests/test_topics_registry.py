"""platform/kafka/topics.yml is the only description of the event backbone.

It is transcribed here independently from spec 01 (the table of partitions,
cleanup policy and retention) and spec 02 (who produces and who consumes), so
a typo in either direction fails the build rather than surfacing as a service
silently reading a topic nobody writes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import _registry
import pytest

DAY_MS = 86_400_000
REPO_ROOT = Path(__file__).resolve().parents[2]
CREATE_TOPICS_SH = REPO_ROOT / "platform" / "kafka" / "create_topics.sh"

# name -> (partitions, cleanup, retention_ms, producers, consumers)
EXPECTED: dict[str, tuple[int, str, int, set[str], set[str]]] = {
    "yadakchi.listings.observed.v1": (6, "delete", 90 * DAY_MS, {"crawler"}, {"enricher"}),
    "yadakchi.offers.enriched.v1": (6, "delete", 90 * DAY_MS, {"enricher"}, {"fitment", "matcher"}),
    "yadakchi.offers.fitted.v1": (6, "delete", 90 * DAY_MS, {"fitment"}, {"matcher", "catalog"}),
    "yadakchi.vehicles.changed.v1": (
        1,
        "compact",
        -1,
        {"fitment"},
        {"matcher", "catalog", "search", "web"},
    ),
    "yadakchi.crossrefs.changed.v1": (3, "compact", -1, {"fitment"}, {"catalog", "search"}),
    "yadakchi.clusters.changed.v1": (6, "delete", 90 * DAY_MS, {"matcher"}, {"catalog"}),
    "yadakchi.products.changed.v1": (6, "compact", -1, {"catalog"}, {"search", "ops", "web"}),
    "yadakchi.sellers.changed.v1": (1, "compact", -1, {"catalog"}, {"billing", "ops"}),
    # crawler consumes clicks too — traffic is what drives hot-tier crawl
    # scheduling (04-CRAWLER.md).
    "yadakchi.clicks.recorded.v1": (
        3,
        "delete",
        30 * DAY_MS,
        {"billing"},
        {"catalog", "matcher", "crawler"},
    ),
    # Five producers: matcher (merge_pair), crawler (adapter_broken), fitment
    # (fitment_conflict), enricher (price_ambiguous, synonym_candidate) and
    # billing (click-velocity anomalies). 11-OPS.md consumes from all five.
    "yadakchi.review.requested.v1": (
        3,
        "delete",
        30 * DAY_MS,
        {"matcher", "crawler", "fitment", "enricher", "billing"},
        {"ops"},
    ),
    "yadakchi.review.decided.v1": (1, "compact", -1, {"ops"}, {"matcher", "fitment"}),
}


@pytest.fixture(scope="module")
def registry() -> _registry.Registry:
    return _registry.load_topics()


def test_all_eleven_topics_declared(registry: _registry.Registry) -> None:
    assert registry.names == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_topic_matches_spec(name: str, registry: _registry.Registry) -> None:
    partitions, cleanup, retention, producers, consumers = EXPECTED[name]
    topic = registry.by_name(name)
    assert topic is not None
    assert topic.partitions == partitions
    assert topic.cleanup == cleanup
    assert topic.retention_ms == retention
    assert set(topic.producers) == producers
    assert set(topic.consumers) == consumers
    assert topic.schema_owner in producers


def test_every_topic_declares_a_message_key(registry: _registry.Registry) -> None:
    """Compaction and per-entity ordering both depend on the key being set."""
    for topic in registry.topics:
        assert topic.key, topic.name


def test_reference_state_is_compacted(registry: _registry.Registry) -> None:
    """Compacted topics are the ones a new consumer rebuilds a read model from."""
    compacted = {t.name for t in registry.topics if t.is_compacted}
    assert compacted == {
        "yadakchi.vehicles.changed.v1",
        "yadakchi.crossrefs.changed.v1",
        "yadakchi.products.changed.v1",
        "yadakchi.sellers.changed.v1",
        "yadakchi.review.decided.v1",
    }
    for topic in registry.topics:
        if topic.is_compacted:
            assert topic.retention_ms == -1, f"{topic.name} must retain forever"


def test_human_decisions_are_never_deleted(registry: _registry.Registry) -> None:
    topic = registry.by_name("yadakchi.review.decided.v1")
    assert topic is not None
    assert topic.is_compacted
    assert topic.retention_ms == -1


def test_dlq_companion_for_every_non_compacted_topic(registry: _registry.Registry) -> None:
    for topic in registry.topics:
        assert topic.dlq is not topic.is_compacted, topic.name
    assert sum(t.dlq for t in registry.topics) == 6


def test_topic_names_are_versioned_and_namespaced(registry: _registry.Registry) -> None:
    for topic in registry.topics:
        assert topic.name.startswith("yadakchi."), topic.name
        assert topic.name.endswith(".v1"), topic.name


def _yaml_plan(registry: _registry.Registry) -> list[tuple[str, str, str, str, str]]:
    return [
        (t.name, str(t.partitions), t.cleanup, str(t.retention_ms), "true" if t.dlq else "false")
        for t in registry.topics
    ]


def test_shell_parser_agrees_with_yaml(registry: _registry.Registry) -> None:
    """create_topics.sh parses topics.yml with awk so it can run inside the
    Kafka image, which has no Python. This proves the two readings agree."""
    plan = subprocess.run(  # noqa: S603
        ["/bin/bash", str(CREATE_TOPICS_SH)],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "DRY_RUN": "1", "KAFKA_BIN": "/nonexistent"},
    )
    planned = [line.split()[1] for line in plan.stdout.splitlines() if line.startswith("PLAN ")]
    expected: list[str] = []
    for topic in registry.topics:
        expected.append(topic.name)
        if topic.dlq:
            expected.append(topic.dlq_name)
    assert planned == expected


def test_shell_plan_carries_the_declared_settings(registry: _registry.Registry) -> None:
    plan = subprocess.run(  # noqa: S603
        ["/bin/bash", str(CREATE_TOPICS_SH)],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "DRY_RUN": "1", "KAFKA_BIN": "/nonexistent"},
    )
    lines = {line.split()[1]: line for line in plan.stdout.splitlines() if line.startswith("PLAN ")}
    for topic in registry.topics:
        line = lines[topic.name]
        assert f"partitions={topic.partitions}" in line
        assert f"cleanup.policy={topic.cleanup}" in line
        assert f"retention.ms={topic.retention_ms}" in line
        if topic.dlq:
            dlq_line = lines[topic.dlq_name]
            assert f"partitions={topic.partitions}" in dlq_line
            assert "cleanup.policy=delete" in dlq_line
