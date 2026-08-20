"""Invariants of the two compose files.

These are cheap to check and expensive to get wrong: an unbounded container, a
service accidentally handed another service's credentials, or infrastructure
quietly growing a piece of application code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA_FILE = REPO_ROOT / "platform" / "docker-compose.infra.yml"
ROOT_FILE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / "platform" / ".env.example"

SERVICES = (
    "ai",
    "crawler",
    "enricher",
    "fitment",
    "matcher",
    "catalog",
    "search",
    "billing",
    "ops",
    "web",
)
ONE_SHOT = {"kafka-data-perms", "kafka-init", "minio-init"}


def load(path: Path) -> dict[str, Any]:
    parsed: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parsed


@pytest.fixture(scope="module")
def infra() -> dict[str, Any]:
    return load(INFRA_FILE)


@pytest.fixture(scope="module")
def root() -> dict[str, Any]:
    return load(ROOT_FILE)


def test_infra_provides_the_declared_components(infra: dict[str, Any]) -> None:
    assert {
        "postgres",
        "kafka",
        "redis",
        "minio",
        "typesense",
        "prometheus",
        "grafana",
    } <= set(infra["services"])


def test_infra_contains_no_service(infra: dict[str, Any]) -> None:
    """The platform owns no service. If one appears here, that is the bug."""
    assert not set(infra["services"]) & set(SERVICES)


def test_every_infra_container_has_a_memory_limit(infra: dict[str, Any]) -> None:
    missing = [name for name, spec in infra["services"].items() if "mem_limit" not in spec]
    assert not missing


def test_long_running_infra_containers_have_healthchecks(infra: dict[str, Any]) -> None:
    missing = [
        name
        for name, spec in infra["services"].items()
        if name not in ONE_SHOT and "healthcheck" not in spec
    ]
    assert not missing


def test_stateful_components_have_named_volumes(infra: dict[str, Any]) -> None:
    assert {
        "postgres_data",
        "kafka_data",
        "redis_data",
        "minio_data",
        "typesense_data",
        "prometheus_data",
        "grafana_data",
    } <= set(infra["volumes"])


def test_everything_joins_the_external_network(infra: dict[str, Any]) -> None:
    network = infra["networks"]["yadakchi"]
    assert network["external"] is True
    assert network["name"] == "yadakchi"
    for name, spec in infra["services"].items():
        assert spec.get("networks") == ["yadakchi"], name


def test_no_secret_is_hardcoded() -> None:
    """Credentials enter through the environment, never through a compose file."""
    for path in (INFRA_FILE, ROOT_FILE):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?im)^\s*[A-Z_]*(PASSWORD|SECRET|API_KEY)\s*:\s*(\S+)", text):
            value = match.group(2)
            assert value.startswith("${"), f"{path.name}: {match.group(0).strip()}"


def test_root_compose_includes_the_platform(root: dict[str, Any]) -> None:
    assert root["include"] == [{"path": "platform/docker-compose.infra.yml"}]


def test_root_compose_declares_all_ten_services(root: dict[str, Any]) -> None:
    assert set(root["services"]) == set(SERVICES)


def test_every_service_has_a_memory_limit(root: dict[str, Any]) -> None:
    missing = [name for name, spec in root["services"].items() if "mem_limit" not in spec]
    assert not missing


def test_each_service_gets_only_its_own_database(root: dict[str, Any]) -> None:
    """No shared env_file: a service is handed its own DSN and nothing else."""
    for name, spec in root["services"].items():
        assert "env_file" not in spec, name
        env = spec.get("environment", {})
        dsn = env.get("DATABASE_URL")
        if name == "ai":
            assert dsn is None, "ai has no database"
            continue
        if name == "web":
            assert dsn is None, "web reads through catalog and search, never a database"
            continue
        assert dsn == f"${{{name.upper()}_DATABASE_URL}}", name


def test_each_service_gets_its_own_redis_database(root: dict[str, Any]) -> None:
    for name, spec in root["services"].items():
        url = spec.get("environment", {}).get("REDIS_URL")
        assert url == f"${{{name.upper()}_REDIS_URL}}", name


def test_only_the_allowed_synchronous_pairs_exist(root: dict[str, Any]) -> None:
    """The brief allows exactly these callers; everything else is Kafka."""
    allowed = {
        "AI_BASE_URL": {"enricher", "matcher", "search"},
        "CATALOG_BASE_URL": {"web", "ops"},
        "SEARCH_BASE_URL": {"web"},
        "BILLING_BASE_URL": {"ops"},
    }
    actual: dict[str, set[str]] = {key: set() for key in allowed}
    for name, spec in root["services"].items():
        for key in spec.get("environment", {}):
            if key in actual:
                actual[key].add(name)
    assert actual == allowed


def test_only_crawler_holds_object_storage_credentials(root: dict[str, Any]) -> None:
    holders = {
        name
        for name, spec in root["services"].items()
        if any(key.startswith("S3_") for key in spec.get("environment", {}))
    }
    assert holders == {"crawler"}


def test_only_search_holds_the_typesense_key(root: dict[str, Any]) -> None:
    holders = {
        name
        for name, spec in root["services"].items()
        if "TYPESENSE_API_KEY" in spec.get("environment", {})
    }
    assert holders == {"search"}


def test_every_interpolated_variable_is_documented() -> None:
    """Anything ${FOO} in a compose file must exist in .env.example, or a fresh
    clone starts with an empty value and a confusing failure."""
    documented = {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    for path in (INFRA_FILE, ROOT_FILE):
        used = set(re.findall(r"\$\{([A-Z0-9_]+)", path.read_text(encoding="utf-8")))
        undocumented = used - documented
        assert not undocumented, f"{path.name}: {sorted(undocumented)}"
