"""Shared fixtures for the platform test suite.

Two kinds of test live here:

  * plain unit tests, which run anywhere and are what CI gates on;
  * tests marked `infra`, which talk to the real containers and are skipped
    unless `make infra-up` has been run. `make verify` runs those.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / "platform" / ".env"
ENV_EXAMPLE = REPO_ROOT / "platform" / ".env.example"


def load_env() -> dict[str, str]:
    """platform/.env if it exists, else the committed example."""
    source = ENV_FILE if ENV_FILE.is_file() else ENV_EXAMPLE
    values: dict[str, str] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split(" #")[0].strip()
    values.update({k: v for k, v in os.environ.items() if k in values})
    return values


@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    return load_env()


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def requires_port(host: str, port: int, what: str) -> None:
    if not port_open(host, port):
        pytest.skip(f"{what} not reachable at {host}:{port} — run `make infra-up`")


@pytest.fixture(scope="session")
def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(  # noqa: S603
        ["docker", "info"],  # noqa: S607
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An isolated stand-in for the repository.

    The guards read module-level paths in `_registry`, so pointing those at a
    temporary tree lets us prove real drift is caught without ever dirtying
    the working copy.
    """
    import _registry

    services = tmp_path / "services"
    specs = tmp_path / "docs" / "specs"
    services.mkdir(parents=True)
    specs.mkdir(parents=True)

    for name in _registry.SERVICES:
        for folder in ("published", "consumed", "examples"):
            (services / name / "contracts" / folder).mkdir(parents=True)

    monkeypatch.setattr(_registry, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_registry, "SERVICES_DIR", services)
    monkeypatch.setattr(_registry, "SPECS_DIR", specs)
    yield tmp_path
