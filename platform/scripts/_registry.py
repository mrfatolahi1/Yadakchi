"""Shared reading of the platform's two registries.

Kept deliberately tiny and dependency-light: the drift guards must be runnable
in CI before a single service exists.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TOPICS_FILE = REPO_ROOT / "platform" / "kafka" / "topics.yml"
APIS_FILE = REPO_ROOT / "platform" / "http" / "apis.yml"
SERVICES_DIR = REPO_ROOT / "services"
SPECS_DIR = REPO_ROOT / "docs" / "specs"

# services/<name> -> docs/specs/<file>. The brief (00) goes to every service.
BRIEF_SPEC = "00-PROJECT-BRIEF.md"
SPEC_MAP: dict[str, str] = {
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
}

SERVICES: tuple[str, ...] = tuple(SPEC_MAP)


@dataclass(frozen=True)
class Topic:
    """One row of platform/kafka/topics.yml."""

    name: str
    partitions: int
    cleanup: str
    retention_ms: int
    key: str
    schema_owner: str
    producers: tuple[str, ...]
    consumers: tuple[str, ...]
    dlq: bool

    @property
    def is_compacted(self) -> bool:
        return self.cleanup == "compact"

    @property
    def dlq_name(self) -> str:
        return f"{self.name}.dlq"

    @property
    def schema_filename(self) -> str:
        return f"{self.name}.json"

    @property
    def other_producers(self) -> tuple[str, ...]:
        """Producers that do not own the schema and therefore hold a copy."""
        return tuple(p for p in self.producers if p != self.schema_owner)

    @property
    def copy_holders(self) -> tuple[str, ...]:
        """Every service that must carry a byte-identical consumed/ copy."""
        seen: dict[str, None] = {}
        for svc in (*self.consumers, *self.other_producers):
            seen[svc] = None
        return tuple(seen)


@dataclass(frozen=True)
class HttpApi:
    """One row of platform/http/apis.yml.

    The OpenAPI counterpart to a Topic: one publisher owns the document, and
    every declared caller vendors a byte-identical copy, because no service may
    read another service's directory to fetch one.
    """

    publisher: str
    consumers: tuple[str, ...]
    purpose: str

    @property
    def published_filename(self) -> str:
        return "openapi.json"

    @property
    def vendored_filename(self) -> str:
        """What the copy is called inside a consumer, e.g. ai-openapi.json."""
        return f"{self.publisher}-openapi.json"


@dataclass
class Registry:
    topics: list[Topic] = field(default_factory=list)

    def by_name(self, name: str) -> Topic | None:
        return next((t for t in self.topics if t.name == name), None)

    @property
    def names(self) -> set[str]:
        return {t.name for t in self.topics}


def load_topics(path: Path = TOPICS_FILE) -> Registry:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    topics: list[Topic] = []
    for entry in raw["topics"]:
        topics.append(
            Topic(
                name=str(entry["name"]),
                partitions=int(entry["partitions"]),
                cleanup=str(entry["cleanup"]),
                retention_ms=int(entry["retention_ms"]),
                key=str(entry["key"]),
                schema_owner=str(entry["schema_owner"]),
                producers=tuple(str(s) for s in entry["producers"]),
                consumers=tuple(str(s) for s in entry["consumers"]),
                dlq=bool(entry["dlq"]),
            )
        )
    return Registry(topics=topics)


def load_apis(path: Path = APIS_FILE) -> list[HttpApi]:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        HttpApi(
            publisher=str(entry["publisher"]),
            consumers=tuple(str(s) for s in entry["consumers"]),
            purpose=str(entry.get("purpose", "")),
        )
        for entry in raw["apis"]
    ]


def vendored_openapi_names() -> set[str]:
    """Every legal <publisher>-openapi.json filename, for the stray check."""
    return {api.vendored_filename for api in load_apis()}


def service_dirs() -> list[str]:
    """Service folders that actually exist on disk, in spec order."""
    return [name for name in SERVICES if (SERVICES_DIR / name).is_dir()]


def published_dir(service: str) -> Path:
    return SERVICES_DIR / service / "contracts" / "published"


def consumed_dir(service: str) -> Path:
    return SERVICES_DIR / service / "contracts" / "consumed"


def rel(path: Path) -> str:
    """Repo-relative path for messages a human has to act on."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def fail(message: str) -> None:
    print(f"FAIL  {message}", file=sys.stderr)
