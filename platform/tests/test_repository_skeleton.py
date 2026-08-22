"""The repository skeleton itself.

The platform's job is to hand ten agents a working, isolated starting point —
and to write none of their code. These tests hold both halves of that.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
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

# What the platform is allowed to leave inside a service folder. Anything else
# is the service agent's own work.
PLATFORM_OWNED = {"README.md", "BRIEF.md", "SPEC.md", "contracts"}


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "Makefile",
        "docker-compose.yml",
        ".gitignore",
        ".dockerignore",
        ".editorconfig",
        ".github/CODEOWNERS",
        ".github/workflows/ci.yml",
        "platform/docker-compose.infra.yml",
        "platform/.env.example",
        "platform/postgres/init.sql",
        "platform/kafka/topics.yml",
        "platform/kafka/create_topics.sh",
        "platform/minio/init.sh",
        "platform/observability/prometheus.yml",
        "platform/scripts/check_contracts.py",
        "platform/scripts/wait_for.py",
    ],
)
def test_skeleton_file_exists(path: str) -> None:
    assert (REPO_ROOT / path).is_file(), path


def test_grafana_is_provisioned() -> None:
    grafana = REPO_ROOT / "platform" / "observability" / "grafana"
    assert (grafana / "provisioning" / "datasources").is_dir()
    assert list((grafana / "dashboards").glob("*.json")), "no starter dashboard"


@pytest.mark.parametrize("service", SERVICES)
def test_service_folder_is_self_sufficient(service: str) -> None:
    """Acceptance criterion 13: a fresh clone, no setup, everything present."""
    folder = REPO_ROOT / "services" / service
    assert (folder / "BRIEF.md").is_file()
    assert (folder / "SPEC.md").is_file()
    assert (folder / "README.md").is_file()
    assert (folder / "contracts" / "published").is_dir()
    assert (folder / "contracts" / "consumed").is_dir()


@pytest.mark.parametrize("service", SERVICES)
def test_platform_wrote_no_service_code(service: str) -> None:
    """The platform owns no service. Spec 01 says: if you find yourself writing
    a crawler or a model, stop."""
    folder = REPO_ROOT / "services" / service
    unexpected = {p.name for p in folder.iterdir()} - PLATFORM_OWNED
    assert not unexpected, f"platform left non-spec files in services/{service}: {unexpected}"


def test_distributed_copies_are_not_gitignored() -> None:
    """A fresh clone must contain them, so they cannot be ignored."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("BRIEF.md", "SPEC.md", "services/"):
        for line in gitignore.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            assert stripped != pattern, f".gitignore excludes {pattern}"


def test_codeowners_protects_specs_platform_and_schemas() -> None:
    codeowners = (REPO_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "/docs/specs/" in codeowners
    assert "/platform/" in codeowners
    assert "/.github/" in codeowners
    assert "contracts/published/" in codeowners


def test_readme_gets_a_newcomer_to_running_infra() -> None:
    """Acceptance criterion 10 — the commands have to actually be in there."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for command in ("make env", "make infra-up", "make topics", "make ci"):
        assert command in readme, command


# ---------------------------------------------------------------------------
# Redis database assignment.
#
# platform/.env.example is what the containers are actually configured from,
# and it is the only complete, collision-free assignment: ten services, db 0-9.
# Each service spec also names a database in prose, and the two had drifted —
# every spec was off by one, and 03-AI.md claimed db 0, which is crawler's.
# Billing hit it first: its spec said db 7, the env said 6, and 7 is ops'. Two
# services sharing a Redis db silently share a keyspace.
#
# .env.example wins. A service reads its own *_REDIS_URL and never hardcodes a
# number; these tests keep the prose honest about which one it will get.
# ---------------------------------------------------------------------------

REDIS_URL_LINE = re.compile(r"^([A-Z]+)_REDIS_URL=.*/(\d+)\s*$")
SPEC_REDIS_DB = re.compile(r"Redis db (\d+)")


def _redis_dbs_from_env() -> dict[str, int]:
    text = (REPO_ROOT / "platform" / ".env.example").read_text(encoding="utf-8")
    found: dict[str, int] = {}
    for line in text.splitlines():
        match = REDIS_URL_LINE.match(line.strip())
        if match is not None:
            found[match.group(1).lower()] = int(match.group(2))
    return found


def test_every_service_has_its_own_redis_database() -> None:
    assigned = _redis_dbs_from_env()
    assert set(assigned) == set(SERVICES), "a service has no *_REDIS_URL"

    collisions = {
        db: sorted(s for s, d in assigned.items() if d == db) for db in set(assigned.values())
    }
    shared = {db: names for db, names in collisions.items() if len(names) > 1}
    assert not shared, f"services sharing a Redis keyspace: {shared}"


@pytest.mark.parametrize("service", SERVICES)
def test_spec_redis_db_matches_the_environment(service: str) -> None:
    """A spec may omit the database, but if it names one it must be the real one."""
    spec_file = {
        "ai": "03-AI.md",
        "crawler": "04-CRAWLER.md",
        "enricher": "05-ENRICHER.md",
        "fitment": "06-FITMENT.md",
        "matcher": "07-MATCHER.md",
        "catalog": "08-CATALOG.md",
        "search": "09-SEARCH.md",
        "billing": "10-BILLING.md",
        "ops": "11-OPS.md",
        "web": "12-WEB.md",
    }[service]
    text = (REPO_ROOT / "docs" / "specs" / spec_file).read_text(encoding="utf-8")
    claimed = {int(n) for n in SPEC_REDIS_DB.findall(text)}
    if not claimed:
        return

    actual = _redis_dbs_from_env()[service]
    assert claimed == {actual}, (
        f"{spec_file} says Redis db {sorted(claimed)} but platform/.env.example "
        f"gives {service} db {actual}. The env file configures the containers and "
        "wins; fix the spec."
    )
