"""Every service folder must be self-sufficient, and must stay honest.

A fresh clone has to give an agent its brief and its spec inside its own
folder, and editing either copy has to fail the build — otherwise the specs
silently fork.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import _registry
import pytest
import sync_specs

SERVICES = tuple(_registry.SPEC_MAP)


@pytest.fixture
def fake_specs(fake_repo: Path) -> Path:
    """A temporary repo with real spec sources copied in."""
    for name in (_registry.BRIEF_SPEC, *_registry.SPEC_MAP.values()):
        shutil.copyfile(
            Path(__file__).resolve().parents[2] / "docs" / "specs" / name,
            _registry.SPECS_DIR / name,
        )
    return fake_repo


def test_mapping_covers_the_ten_services() -> None:
    assert len(SERVICES) == 10
    assert set(SERVICES) == set(_registry.SERVICES)
    assert len(set(_registry.SPEC_MAP.values())) == 10, "two services share a spec"


def test_every_mapped_spec_exists() -> None:
    specs = Path(__file__).resolve().parents[2] / "docs" / "specs"
    assert (specs / _registry.BRIEF_SPEC).is_file()
    for filename in _registry.SPEC_MAP.values():
        assert (specs / filename).is_file(), filename


def test_distribution_places_all_three_files(fake_specs: Path) -> None:
    """Acceptance criteria 11 and 13."""
    assert sync_specs.distribute() == 0
    for service in SERVICES:
        folder = _registry.SERVICES_DIR / service
        assert (folder / "BRIEF.md").is_file()
        assert (folder / "SPEC.md").is_file()
        assert (folder / "README.md").is_file()


def test_distribution_is_idempotent(fake_specs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert sync_specs.distribute() == 0
    before = {p: p.read_bytes() for p in _registry.SERVICES_DIR.rglob("*.md")}
    capsys.readouterr()

    assert sync_specs.distribute() == 0
    assert "updated 0" in capsys.readouterr().out
    after = {p: p.read_bytes() for p in _registry.SERVICES_DIR.rglob("*.md")}
    assert before == after


def test_each_service_gets_its_own_spec(fake_specs: Path) -> None:
    sync_specs.distribute()
    for service, source in _registry.SPEC_MAP.items():
        expected = (_registry.SPECS_DIR / source).read_bytes()
        assert (_registry.SERVICES_DIR / service / "SPEC.md").read_bytes() == expected
        brief = (_registry.SPECS_DIR / _registry.BRIEF_SPEC).read_bytes()
        assert (_registry.SERVICES_DIR / service / "BRIEF.md").read_bytes() == brief


def test_readme_points_at_both_and_fences_the_agent(fake_specs: Path) -> None:
    sync_specs.distribute()
    readme = (_registry.SERVICES_DIR / "search" / "README.md").read_text(encoding="utf-8")
    assert "BRIEF.md" in readme
    assert "SPEC.md" in readme
    assert "services/search/" in readme
    assert "Do not edit them here" in readme


def test_readme_is_not_clobbered(fake_specs: Path) -> None:
    """Scaffolding, not a copy: a service agent may extend its own README."""
    sync_specs.distribute()
    readme = _registry.SERVICES_DIR / "ops" / "README.md"
    readme.write_text("# ops\n\nlocal notes\n", encoding="utf-8")
    sync_specs.distribute()
    assert readme.read_text(encoding="utf-8") == "# ops\n\nlocal notes\n"


def test_edited_spec_copy_fails_check(fake_specs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Acceptance criterion 12, the exact scenario in the spec."""
    sync_specs.distribute()
    drifted = _registry.SERVICES_DIR / "search" / "SPEC.md"
    drifted.write_bytes(drifted.read_bytes() + b"\nsneaky local edit\n")

    assert sync_specs.check() == 1
    stderr = capsys.readouterr().err
    assert "services/search/SPEC.md" in stderr
    assert "drifted" in stderr


def test_edited_brief_copy_fails_check(
    fake_specs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sync_specs.distribute()
    drifted = _registry.SERVICES_DIR / "billing" / "BRIEF.md"
    drifted.write_bytes(drifted.read_bytes().replace(b"Iran", b"Irran", 1))
    assert sync_specs.check() == 1
    assert "services/billing/BRIEF.md" in capsys.readouterr().err


def test_missing_copy_fails_check(fake_specs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sync_specs.distribute()
    (_registry.SERVICES_DIR / "web" / "SPEC.md").unlink()
    assert sync_specs.check() == 1
    assert "services/web/SPEC.md" in capsys.readouterr().err


def test_resync_repairs_drift(fake_specs: Path) -> None:
    sync_specs.distribute()
    drifted = _registry.SERVICES_DIR / "matcher" / "SPEC.md"
    drifted.write_text("wrong", encoding="utf-8")
    assert sync_specs.check() == 1
    assert sync_specs.distribute() == 0
    assert sync_specs.check() == 0


def test_committed_repository_is_in_sync() -> None:
    """The real working copy, as committed."""
    assert sync_specs.check() == 0
