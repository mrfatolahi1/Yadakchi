"""Path filtering has to actually filter. Acceptance criterion 8."""

from __future__ import annotations

import ci_matrix
import pytest


def test_service_change_runs_only_that_service() -> None:
    result = ci_matrix.plan(["services/matcher/matching/blocking.py"])
    assert result["python_services"] == ["matcher"]
    assert result["web"] is False


def test_unrelated_services_are_skipped() -> None:
    result = ci_matrix.plan(["services/catalog/api.py", "services/catalog/tests/test_api.py"])
    assert result["python_services"] == ["catalog"]
    for skipped in ("matcher", "crawler", "enricher", "ai"):
        assert skipped not in result["python_services"]


def test_two_services_run_two_jobs() -> None:
    result = ci_matrix.plan(["services/ai/app/main.py", "services/search/index.py"])
    assert set(result["python_services"]) == {"ai", "search"}


def test_web_is_its_own_job() -> None:
    result = ci_matrix.plan(["services/web/app/page.tsx"])
    assert result["web"] is True
    assert result["python_services"] == []


def test_platform_change_runs_everything() -> None:
    result = ci_matrix.plan(["platform/docker-compose.infra.yml"])
    assert set(result["python_services"]) == set(ci_matrix.PYTHON_SERVICES)
    assert result["web"] is True


@pytest.mark.parametrize(
    "path", ["Makefile", "docker-compose.yml", "pyproject.toml", ".github/workflows/ci.yml"]
)
def test_root_files_run_everything(path: str) -> None:
    assert ci_matrix.plan([path])["web"] is True


def test_spec_change_runs_everything() -> None:
    """Specs are distributed into every service folder, so they all rebuild."""
    result = ci_matrix.plan(["docs/specs/07-MATCHER.md"])
    assert len(result["python_services"]) == len(ci_matrix.PYTHON_SERVICES)


def test_documentation_only_change_runs_no_service_job() -> None:
    result = ci_matrix.plan(["README.md"])
    assert result["python_services"] == []
    assert result["web"] is False
    assert result["any_service"] is False


def test_ordering_is_stable() -> None:
    """Matrix order should not flap between runs."""
    a = ci_matrix.plan(["services/web/x.ts", "services/ai/y.py", "services/crawler/z.py"])
    b = ci_matrix.plan(["services/crawler/z.py", "services/ai/y.py", "services/web/x.ts"])
    assert a == b
