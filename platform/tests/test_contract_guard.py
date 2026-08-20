"""The contract drift guard must catch every way ten agents can disagree.

These tests build a throwaway repository, so they exercise the real failure
paths without touching the working copy.
"""

from __future__ import annotations

import json
from pathlib import Path

import _registry
import check_contracts
import pytest
import sync_contracts

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "yadakchi.offers.enriched.v1",
    "type": "object",
    "additionalProperties": True,
    "required": ["offer_uid", "price_toman"],
    "properties": {
        "offer_uid": {"type": "string"},
        "price_toman": {"type": ["integer", "null"]},
    },
}

TOPIC = "yadakchi.offers.enriched.v1"
OWNER = "enricher"
READERS = ("fitment", "matcher")


def write_schema(repo: Path, service: str, folder: str, topic: str, payload: object) -> Path:
    path = _registry.SERVICES_DIR / service / "contracts" / folder / f"{topic}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def publish_and_sync(repo: Path) -> Path:
    source = write_schema(repo, OWNER, "published", TOPIC, SCHEMA)
    assert sync_contracts.main([]) == 0
    return source


def test_empty_skeleton_passes(fake_repo: Path) -> None:
    """Before spec 02 lands nothing is published. That is a valid repository."""
    assert check_contracts.main([]) == 0


def test_registry_matches_reality(fake_repo: Path) -> None:
    """Every service named in topics.yml has a folder in the skeleton."""
    registry = _registry.load_topics()
    known = set(_registry.service_dirs())
    for topic in registry.topics:
        assert set(topic.producers) <= known, topic.name
        assert set(topic.consumers) <= known, topic.name


def test_sync_then_check_passes(fake_repo: Path) -> None:
    publish_and_sync(fake_repo)
    assert check_contracts.main([]) == 0
    for reader in READERS:
        assert (_registry.consumed_dir(reader) / f"{TOPIC}.json").is_file()


def test_sync_is_idempotent(fake_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish_and_sync(fake_repo)
    capsys.readouterr()
    assert sync_contracts.main([]) == 0
    assert "updated 0" in capsys.readouterr().out


def test_one_byte_of_drift_fails(fake_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Acceptance criterion 7: edit a consumed copy by one byte -> CI fails."""
    publish_and_sync(fake_repo)
    copy = _registry.consumed_dir("matcher") / f"{TOPIC}.json"
    original = copy.read_bytes()
    copy.write_bytes(original.replace(b"price_toman", b"price_tomans", 1))
    assert len(copy.read_bytes()) == len(original) + 1

    assert check_contracts.main([]) == 1
    stderr = capsys.readouterr().err
    assert "matcher" in stderr
    assert TOPIC in stderr
    assert "drifted" in stderr


def test_whitespace_only_drift_fails(fake_repo: Path) -> None:
    """Byte-identical means byte-identical — a reformat is drift too."""
    publish_and_sync(fake_repo)
    copy = _registry.consumed_dir("fitment") / f"{TOPIC}.json"
    copy.write_text(json.dumps(SCHEMA) + "\n", encoding="utf-8")
    assert check_contracts.main([]) == 1


def test_missing_consumer_copy_fails(fake_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish_and_sync(fake_repo)
    (_registry.consumed_dir("fitment") / f"{TOPIC}.json").unlink()
    assert check_contracts.main([]) == 1
    assert "fitment" in capsys.readouterr().err


def test_two_publishers_fail(fake_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Exactly one service owns a schema, even for a topic several produce to."""
    publish_and_sync(fake_repo)
    write_schema(fake_repo, "crawler", "published", TOPIC, SCHEMA)
    assert check_contracts.main([]) == 1
    stderr = capsys.readouterr().err
    assert "publishers" in stderr
    assert "crawler" in stderr


def test_publisher_that_registry_did_not_authorise_fails(fake_repo: Path) -> None:
    write_schema(fake_repo, "catalog", "published", TOPIC, SCHEMA)
    assert check_contracts.main([]) == 1


def test_undeclared_consumer_copy_fails(
    fake_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A service quietly starting to read someone else's topic is caught."""
    publish_and_sync(fake_repo)
    write_schema(fake_repo, "billing", "consumed", TOPIC, SCHEMA)
    assert check_contracts.main([]) == 1
    assert "billing" in capsys.readouterr().err


def test_unknown_topic_fails(fake_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An invented topic must be declared in topics.yml first."""
    write_schema(fake_repo, "matcher", "published", "yadakchi.invented.v1", SCHEMA)
    assert check_contracts.main([]) == 1
    assert "yadakchi.invented.v1" in capsys.readouterr().err


def test_malformed_schema_fails(fake_repo: Path) -> None:
    path = _registry.published_dir(OWNER) / f"{TOPIC}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert check_contracts.main([]) == 1


def test_multi_producer_topic_requires_copies(fake_repo: Path) -> None:
    """review.requested has three producers; the two that do not own the
    schema must still carry a byte-identical copy."""
    topic = _registry.load_topics().by_name("yadakchi.review.requested.v1")
    assert topic is not None
    assert topic.schema_owner == "matcher"
    assert set(topic.copy_holders) == {"ops", "crawler", "fitment"}

    write_schema(fake_repo, topic.schema_owner, "published", topic.name, SCHEMA)
    assert check_contracts.main([]) == 1  # copies missing
    assert sync_contracts.main([]) == 0
    assert check_contracts.main([]) == 0


def test_real_repository_is_clean() -> None:
    """The committed repository itself must pass, always."""
    assert check_contracts.main([]) == 0
