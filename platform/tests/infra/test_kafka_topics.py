"""The event backbone must match platform/kafka/topics.yml exactly, and
applying it must be a no-op the second time. Acceptance criteria 3 and 4.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import _registry
import pytest
from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, ConfigResource, NewTopic
from conftest import requires_port

pytestmark = pytest.mark.infra

REPO_ROOT = Path(__file__).resolve().parents[3]
CREATE_TOPICS_SH = REPO_ROOT / "platform" / "kafka" / "create_topics.sh"


@pytest.fixture(scope="module")
def bootstrap(env: dict[str, str]) -> str:
    host, _, port = env.get("KAFKA_BOOTSTRAP_SERVERS_HOST", "localhost:19092").partition(":")
    requires_port("127.0.0.1", int(port), "kafka")
    return f"127.0.0.1:{port}"


@pytest.fixture(scope="module")
def admin(bootstrap: str) -> AdminClient:
    return AdminClient({"bootstrap.servers": bootstrap})


@pytest.fixture(scope="module")
def registry() -> _registry.Registry:
    return _registry.load_topics()


@pytest.fixture(scope="module")
def cluster_topics(admin: AdminClient) -> dict[str, Any]:
    return dict(admin.list_topics(timeout=30).topics)


def topic_config(admin: AdminClient, name: str) -> dict[str, str]:
    resource = ConfigResource(ConfigResource.Type.TOPIC, name)
    future = admin.describe_configs([resource])[resource]
    return {key: entry.value for key, entry in future.result(timeout=30).items()}


def test_every_declared_topic_exists(
    registry: _registry.Registry, cluster_topics: dict[str, Any]
) -> None:
    missing = [t.name for t in registry.topics if t.name not in cluster_topics]
    assert not missing, f"missing topics: {missing} — run `make topics`"


def test_every_non_compacted_topic_has_a_dlq(
    registry: _registry.Registry, cluster_topics: dict[str, Any]
) -> None:
    expected = {t.dlq_name for t in registry.topics if t.dlq}
    assert expected <= set(cluster_topics)
    assert not any(t.dlq_name in cluster_topics for t in registry.topics if not t.dlq)


@pytest.mark.parametrize("topic_name", sorted(t.name for t in _registry.load_topics().topics))
def test_topic_settings_match_the_registry(
    topic_name: str,
    registry: _registry.Registry,
    admin: AdminClient,
    cluster_topics: dict[str, Any],
) -> None:
    declared = registry.by_name(topic_name)
    assert declared is not None
    assert len(cluster_topics[topic_name].partitions) == declared.partitions

    config = topic_config(admin, topic_name)
    assert config["cleanup.policy"] == declared.cleanup
    assert int(config["retention.ms"]) == declared.retention_ms


def test_applying_topics_twice_is_a_no_op() -> None:
    """Acceptance criterion 3: idempotent. Nothing is created on the second run."""
    first = subprocess.run(  # noqa: S603
        ["/bin/bash", str(CREATE_TOPICS_SH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
        timeout=300,
    )
    second = subprocess.run(  # noqa: S603
        ["/bin/bash", str(CREATE_TOPICS_SH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
        timeout=300,
    )
    created = [line for line in second.stdout.splitlines() if line.startswith("create ")]
    altered = [line for line in second.stdout.splitlines() if line.startswith("alter ")]
    assert not created, f"second run created topics: {created}"
    assert not altered, f"second run altered topics: {altered}"
    assert first.stdout.count("reconciled") == 1


def test_auto_topic_creation_is_disabled(bootstrap: str) -> None:
    """A typo in a consumer must not conjure a topic into existence."""
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"platform-test-{uuid.uuid4()}",
            "auto.offset.reset": "earliest",
            "allow.auto.create.topics": True,
        }
    )
    ghost = f"yadakchi.typo.{uuid.uuid4().hex[:8]}.v1"
    consumer.subscribe([ghost])
    for _ in range(10):
        consumer.poll(0.5)
    consumer.close()

    admin = AdminClient({"bootstrap.servers": bootstrap})
    assert ghost not in admin.list_topics(timeout=30).topics


def test_compaction_keeps_only_the_last_value_for_a_key(bootstrap: str) -> None:
    """Acceptance criterion 4.

    Compacted topics carry reference state: a consumer that reads from the
    beginning rebuilds a read model, so a key written twice must survive as one
    record. Compaction only touches closed segments, so the topic here uses an
    aggressive cleaner configuration to make the same behaviour observable in
    seconds instead of hours.
    """
    admin = AdminClient({"bootstrap.servers": bootstrap})
    name = f"yadakchi.test.compaction.{uuid.uuid4().hex[:8]}.v1"
    topic = NewTopic(
        name,
        num_partitions=1,
        replication_factor=1,
        config={
            "cleanup.policy": "compact",
            "min.cleanable.dirty.ratio": "0.0",
            "min.compaction.lag.ms": "0",
            "delete.retention.ms": "100",
            "segment.ms": "100",
            "segment.bytes": "1024",
        },
    )
    admin.create_topics([topic])[name].result(timeout=60)

    try:
        producer = Producer({"bootstrap.servers": bootstrap})
        key = b"peugeot-206-type-5"
        producer.produce(name, key=key, value=b'{"version": 1}')
        producer.produce(name, key=key, value=b'{"version": 2}')
        producer.flush(30)

        # Roll the active segment so the cleaner can see the duplicates.
        time.sleep(1.0)
        producer.produce(name, key=b"pride-131", value=b'{"version": 1}')
        producer.flush(30)
        time.sleep(1.0)
        producer.produce(name, key=b"pride-131", value=b'{"version": 1}')
        producer.flush(30)

        deadline = time.monotonic() + 180
        records: list[tuple[bytes, bytes]] = []
        while time.monotonic() < deadline:
            records = _read_from_beginning(bootstrap, name)
            if sum(1 for k, _ in records if k == key) == 1:
                break
            time.sleep(5)

        for_key = [value for k, value in records if k == key]
        assert for_key == [b'{"version": 2}'], f"compaction did not collapse the key: {records}"
    finally:
        admin.delete_topics([name])


def _read_from_beginning(bootstrap: str, topic: str) -> list[tuple[bytes, bytes]]:
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"platform-test-{uuid.uuid4()}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    records: list[tuple[bytes, bytes]] = []
    idle = 0
    while idle < 6:
        message = consumer.poll(1.0)
        if message is None:
            idle += 1
            continue
        if message.error():
            idle += 1
            continue
        idle = 0
        records.append((message.key(), message.value()))
    consumer.close()
    return records
