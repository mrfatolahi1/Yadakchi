"""The repository skeleton itself.

The platform's job is to hand ten agents a working, isolated starting point —
and to write none of their code. These tests hold both halves of that.
"""

from __future__ import annotations

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
