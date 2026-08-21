"""platform/kafka/topics.yml is the only description of the event backbone.

It is transcribed here independently from spec 01 (the table of partitions,
cleanup policy and retention) and spec 02 (who produces and who consumes), so
a typo in either direction fails the build rather than surfacing as a service
silently reading a topic nobody writes.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import _registry
import pytest

DAY_MS = 86_400_000
REPO_ROOT = Path(__file__).resolve().parents[2]
CREATE_TOPICS_SH = REPO_ROOT / "platform" / "kafka" / "create_topics.sh"

# name -> (partitions, cleanup, retention_ms, producers, consumers)
EXPECTED: dict[str, tuple[int, str, int, set[str], set[str]]] = {
    "yadakchi.listings.observed.v2": (6, "delete", 90 * DAY_MS, {"crawler"}, {"enricher"}),
    # catalog too: this is its only source of title, price, stock, seller and url.
    "yadakchi.offers.enriched.v1": (
        6,
        "delete",
        90 * DAY_MS,
        {"enricher"},
        {"fitment", "matcher", "catalog"},
    ),
    "yadakchi.offers.fitted.v1": (6, "delete", 90 * DAY_MS, {"fitment"}, {"matcher", "catalog"}),
    "yadakchi.vehicles.changed.v1": (
        1,
        "compact",
        -1,
        {"fitment"},
        {"matcher", "catalog", "search", "web"},
    ),
    # matcher too: crossrefs are a blocking channel and a scoring feature.
    "yadakchi.crossrefs.changed.v1": (
        3,
        "compact",
        -1,
        {"fitment"},
        {"catalog", "search", "matcher"},
    ),
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
    "yadakchi.seller_billing.changed.v1": (1, "compact", -1, {"billing"}, {"catalog"}),
    "yadakchi.review.requested.v1": (
        3,
        "delete",
        30 * DAY_MS,
        {"matcher", "crawler", "fitment", "enricher", "billing"},
        {"ops"},
    ),
    # search too: approved synonyms reach query expansion only through here.
    "yadakchi.review.decided.v1": (1, "compact", -1, {"ops"}, {"matcher", "fitment", "search"}),
}


@pytest.fixture(scope="module")
def registry() -> _registry.Registry:
    return _registry.load_topics()


def test_all_declared_topics_are_transcribed(registry: _registry.Registry) -> None:
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
        "yadakchi.seller_billing.changed.v1",
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
    """Namespaced and version-suffixed. Not every topic is on .v1 any more:
    listings.observed went to .v2 when content_hash became fragment_hash."""
    for topic in registry.topics:
        assert topic.name.startswith("yadakchi."), topic.name
        assert re.fullmatch(r"yadakchi\.[a-z._]+\.v[1-9][0-9]*", topic.name), topic.name


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


# ---------------------------------------------------------------------------
# The registry against the service specs.
#
# topics.yml and each service's "How it connects" table are two independent
# statements of the same routing, and nothing compared them. Three services
# were blocked by the gap in turn: crawler (missing clicks.recorded), catalog
# (missing offers.enriched — which left it with no source for any product
# field at all) and search (missing review.decided). Each cost an agent a full
# stop-and-report cycle before anyone noticed the registry was wrong.
#
# A service spec is the authority on that service's own edges. If these two
# disagree, one of them is a bug — fail here rather than in an agent's inbox.
# ---------------------------------------------------------------------------

TOPIC_IN_TEXT = re.compile(r"yadakchi\.[a-z._]+\.v[1-9][0-9]*")
EDGE_ROW = re.compile(r"^\|\s*\*\*(consumes|produces)\*\*\s*\|")

# web is listed in topics.yml as a consumer of vehicles.changed and
# products.changed, but 12-WEB.md says it owns "nothing. No database, no
# Kafka" and takes everything over HTTP from catalog and search. Unlike the
# three above, this one blocks nobody: web holds two consumed/ copies it will
# never read. Removing a declared consumer deletes files and is a call for the
# spec owner, so it is exempted loudly here rather than fixed quietly.
SPEC_REGISTRY_EXEMPT: dict[str, str] = {
    "web": "12-WEB.md declares no Kafka; topics.yml lists it on two topics. Unresolved.",
}


def _edge_rows(service: str) -> list[tuple[str, set[str], set[str]]]:
    """(direction, peers, topics) for each Kafka row of a spec's edge table."""
    path = _registry.SPECS_DIR / _registry.SPEC_MAP[service]
    rows: list[tuple[str, set[str], set[str]]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = EDGE_ROW.match(line)
        if row is None or "Kafka" not in line:
            continue
        cells = line.split("|")
        peers = {n for n in re.findall(r"`([a-z]+)`", cells[2]) if n in _registry.SERVICES}
        rows.append((row.group(1), peers, set(TOPIC_IN_TEXT.findall(line))))
    return rows


def _edges_declared_in_spec(service: str) -> dict[str, set[str]]:
    """The Kafka topics a service's own spec says it consumes and produces."""
    edges: dict[str, set[str]] = {"consumes": set(), "produces": set()}
    for direction, _peers, topics in _edge_rows(service):
        edges[direction].update(topics)
    return edges


@pytest.mark.parametrize("service", _registry.SERVICES)
def test_spec_edges_match_the_registry(service: str, registry: _registry.Registry) -> None:
    if service in SPEC_REGISTRY_EXEMPT:
        pytest.skip(f"{service}: {SPEC_REGISTRY_EXEMPT[service]}")

    declared = _edges_declared_in_spec(service)
    actual = {
        "consumes": {t.name for t in registry.topics if service in t.consumers},
        "produces": {t.name for t in registry.topics if service in t.producers},
    }
    for direction in ("consumes", "produces"):
        missing = declared[direction] - actual[direction]
        extra = actual[direction] - declared[direction]
        assert not missing, (
            f"{_registry.SPEC_MAP[service]} says {service} {direction} "
            f"{sorted(missing)}, but topics.yml does not — the service cannot get a "
            "consumed/ copy until the registry agrees"
        )
        assert not extra, (
            f"topics.yml has {service} {direction} {sorted(extra)}, but "
            f"{_registry.SPEC_MAP[service]} never declares it"
        )


@pytest.mark.parametrize("service", _registry.SERVICES)
def test_spec_edge_peers_match_the_registry(service: str, registry: _registry.Registry) -> None:
    """The Peer column has to be right too, not just the topic name.

    Adding crawler to clicks.recorded updated the registry and every schema, but
    10-BILLING.md's row went on saying the topic goes to `catalog`, `matcher` —
    and the first guard passed it, because billing does still produce the topic.
    A stale peer list is how a service learns the wrong set of downstreams.
    """
    if service in SPEC_REGISTRY_EXEMPT:
        pytest.skip(f"{service}: {SPEC_REGISTRY_EXEMPT[service]}")

    for direction, peers, topics in _edge_rows(service):
        for name in topics:
            topic = registry.by_name(name)
            assert topic is not None, f"{service}: unknown topic {name}"
            expected = set(topic.consumers) if direction == "produces" else set(topic.producers)
            # A row may cover several topics with one peer list, so the row's
            # peers must be a subset of what the registry allows, and every
            # registry peer must appear somewhere in the rows for that topic.
            assert peers <= expected, (
                f"{_registry.SPEC_MAP[service]}: row for {name} lists peers "
                f"{sorted(peers - expected)} that the registry does not have as "
                f"{'consumers' if direction == 'produces' else 'producers'}"
            )

    for direction in ("consumes", "produces"):
        for topic in registry.topics:
            mine = service in (topic.producers if direction == "produces" else topic.consumers)
            if not mine:
                continue
            expected = set(topic.consumers) if direction == "produces" else set(topic.producers)
            listed: set[str] = set()
            for row_dir, peers, topics in _edge_rows(service):
                if row_dir == direction and topic.name in topics:
                    listed |= peers
            missing = expected - listed - {service}
            assert not missing, (
                f"{_registry.SPEC_MAP[service]}: {direction} row for {topic.name} "
                f"omits {sorted(missing)} — the registry has them, so this spec is stale"
            )


def test_the_exemption_list_does_not_quietly_grow(registry: _registry.Registry) -> None:
    """One known disagreement, named above. A second one is a regression."""
    assert set(SPEC_REGISTRY_EXEMPT) == {"web"}
