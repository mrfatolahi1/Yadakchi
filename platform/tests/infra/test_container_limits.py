"""Every container runs with a memory limit, and every one of them is healthy.

Acceptance criteria 1 and 9. An unbounded container on a shared host is how one
runaway consumer takes down the whole pipeline.
"""

from __future__ import annotations

import json
import subprocess

import pytest

pytestmark = pytest.mark.infra

# The long-running infrastructure. The three init containers exit on success
# and are deliberately excluded.
EXPECTED_CONTAINERS = {
    "yadakchi-postgres",
    "yadakchi-kafka",
    "yadakchi-kafka-ui",
    "yadakchi-kafka-exporter",
    "yadakchi-redis",
    "yadakchi-minio",
    "yadakchi-typesense",
    "yadakchi-prometheus",
    "yadakchi-grafana",
}


def _inspect() -> dict[str, dict[str, object]]:
    names = subprocess.run(  # noqa: S603
        ["docker", "ps", "--filter", "name=yadakchi-", "--format", "{{.Names}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.split()
    if not names:
        pytest.skip("no yadakchi containers running — run `make infra-up`")

    raw = subprocess.run(  # noqa: S603
        ["docker", "inspect", *names],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    return {item["Name"].lstrip("/"): item for item in json.loads(raw)}


def test_every_infrastructure_container_is_running() -> None:
    running = set(_inspect())
    missing = EXPECTED_CONTAINERS - running
    assert not missing, f"not running: {sorted(missing)}"


def test_every_container_is_healthy() -> None:
    """Acceptance criterion 1: `make infra-up` brings everything to healthy."""
    unhealthy: list[str] = []
    for name, item in _inspect().items():
        state = item["State"]
        assert isinstance(state, dict)
        health = state.get("Health")
        if health is None:
            unhealthy.append(f"{name}: no healthcheck")
        elif health["Status"] != "healthy":
            unhealthy.append(f"{name}: {health['Status']}")
    assert not unhealthy, f"not healthy: {unhealthy}"


def test_every_container_has_a_memory_limit() -> None:
    """Acceptance criterion 9, read from the container's own cgroup config."""
    unlimited: list[str] = []
    for name, item in _inspect().items():
        host_config = item["HostConfig"]
        assert isinstance(host_config, dict)
        if not host_config.get("Memory"):
            unlimited.append(name)
    assert not unlimited, f"no mem_limit: {sorted(unlimited)}"


def test_limits_match_the_compose_file() -> None:
    """The big three are sized for a 64 GB host; drift here means the compose
    file and the running system disagree."""
    gib = 1024**3
    expected = {
        "yadakchi-postgres": 24 * gib,
        "yadakchi-kafka": 8 * gib,
        "yadakchi-typesense": 6 * gib,
        "yadakchi-redis": 4 * gib,
        "yadakchi-minio": 2 * gib,
    }
    inspected = _inspect()
    for name, limit in expected.items():
        if name not in inspected:
            continue
        host_config = inspected[name]["HostConfig"]
        assert isinstance(host_config, dict)
        assert host_config["Memory"] == limit, name
