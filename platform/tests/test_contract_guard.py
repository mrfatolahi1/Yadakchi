"""The contract drift guard must catch every way ten agents can disagree.

These tests build a throwaway repository, so they exercise the real failure
paths without touching the working copy.
"""

from __future__ import annotations

import json
import re
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


def test_two_publishers_are_both_named(fake_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A topic with several producers is where this goes wrong in practice.

    review.requested.v1 is produced by five services and owned by one. If a
    producer's own spec tells its agent to put the schema in published/, the
    repository ends up with two owners of one wire format — so the failure has
    to name every service holding a copy, not just the first one found.
    """
    topic = _registry.load_topics().by_name("yadakchi.review.requested.v1")
    assert topic is not None
    assert topic.schema_owner == "matcher"

    write_schema(fake_repo, "matcher", "published", topic.name, SCHEMA)
    write_schema(fake_repo, "fitment", "published", topic.name, SCHEMA)

    assert check_contracts.main([]) == 1
    stderr = capsys.readouterr().err
    offending = [line for line in stderr.splitlines() if "publishers" in line]
    assert len(offending) == 1, stderr
    assert "2 publishers" in offending[0]
    assert topic.name in offending[0]

    # Both services must appear in the list of offenders, not just the first
    # one found — otherwise the message sends someone to fix half the problem.
    listed = re.search(r"\(([^)]*)\)", offending[0])
    assert listed is not None, offending[0]
    assert {name.strip() for name in listed.group(1).split(",")} == {"matcher", "fitment"}


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


def test_openapi_documents_are_not_mistaken_for_topics(fake_repo: Path) -> None:
    """An OpenAPI document in a contracts/ folder must not be read as an
    invented topic. It is not merely ignored any more — it is checked against
    platform/http/apis.yml — so publishing one and syncing must come out clean."""
    publish_and_sync(fake_repo)
    write_schema(fake_repo, "ai", "published", "openapi", {"openapi": "3.1.0"})
    write_schema(fake_repo, "catalog", "published", "openapi", {"openapi": "3.1.0"})
    assert sync_contracts.main([]) == 0
    assert check_contracts.main([]) == 0
    assert (_registry.consumed_dir("web") / "catalog-openapi.json").is_file()
    assert (_registry.consumed_dir("ops") / "catalog-openapi.json").is_file()


def test_an_invented_topic_is_still_caught_next_to_an_openapi_document(
    fake_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The OpenAPI exemption must not become a hole an invented topic fits through."""
    write_schema(fake_repo, "ai", "published", "openapi", {"openapi": "3.1.0"})
    write_schema(fake_repo, "ai", "published", "openapi-ish", SCHEMA)
    assert check_contracts.main([]) == 1
    assert "openapi-ish" in capsys.readouterr().err


def test_malformed_schema_fails(fake_repo: Path) -> None:
    path = _registry.published_dir(OWNER) / f"{TOPIC}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert check_contracts.main([]) == 1


def test_multi_producer_topic_requires_copies(fake_repo: Path) -> None:
    """review.requested has five producers; the four that do not own the
    schema must still carry a byte-identical copy, exactly like a consumer."""
    topic = _registry.load_topics().by_name("yadakchi.review.requested.v1")
    assert topic is not None
    assert topic.schema_owner == "matcher"
    assert set(topic.copy_holders) == {"ops", "crawler", "fitment", "enricher", "billing"}

    write_schema(fake_repo, topic.schema_owner, "published", topic.name, SCHEMA)
    assert check_contracts.main([]) == 1  # copies missing
    assert sync_contracts.main([]) == 0
    assert check_contracts.main([]) == 0


def test_real_repository_is_clean() -> None:
    """The committed repository itself must pass, always."""
    assert check_contracts.main([]) == 0


# ---------------------------------------------------------------------------
# HTTP API documents. Kafka schemas had a distribution mechanism from day one;
# OpenAPI documents did not, so a service told to "vendor the publisher's
# openapi.json" had no legal way to obtain it. search hit this first. They now
# ride the same sync-and-verify path, declared in platform/http/apis.yml.
# ---------------------------------------------------------------------------

OPENAPI = {"openapi": "3.1.0", "info": {"title": "ai", "version": "1"}, "paths": {}}


def publish_openapi(repo: Path, service: str, payload: object = OPENAPI) -> Path:
    return write_schema(repo, service, "published", "openapi", payload)


def test_openapi_is_distributed_to_every_declared_caller(fake_repo: Path) -> None:
    publish_openapi(fake_repo, "ai")
    assert check_contracts.main([]) == 1  # callers have no copy yet
    assert sync_contracts.main([]) == 0
    for caller in ("enricher", "matcher", "search"):
        copy = _registry.consumed_dir(caller) / "ai-openapi.json"
        assert copy.is_file(), caller
    assert check_contracts.main([]) == 0


def test_a_drifted_openapi_copy_fails(fake_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish_openapi(fake_repo, "ai")
    assert sync_contracts.main([]) == 0
    copy = _registry.consumed_dir("search") / "ai-openapi.json"
    copy.write_text(json.dumps({"openapi": "3.1.0", "paths": {"/v1/embed": {}}}), encoding="utf-8")

    assert check_contracts.main([]) == 1
    stderr = capsys.readouterr().err
    assert "search" in stderr
    assert "OpenAPI" in stderr


def test_an_unpublished_openapi_is_a_valid_state(fake_repo: Path) -> None:
    """search has not shipped its document yet; web is not in breach for that."""
    assert check_contracts.main([]) == 0


def test_vendoring_an_undeclared_openapi_fails(
    fake_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The HTTP version of quietly reading someone else's topic."""
    write_schema(fake_repo, "crawler", "consumed", "catalog-openapi", OPENAPI)
    assert check_contracts.main([]) == 1
    stderr = capsys.readouterr().err
    assert "crawler" in stderr
    assert "not declared as a caller" in stderr


def test_openapi_pairs_match_the_brief(fake_repo: Path) -> None:
    """apis.yml transcribes 00-PROJECT-BRIEF.md's allowed synchronous pairs.
    Adding one here does not make it allowed — the brief changes first."""
    declared = {api.publisher: set(api.consumers) for api in _registry.load_apis()}
    assert declared == {
        "ai": {"enricher", "matcher", "search"},
        "catalog": {"web", "ops"},
        "search": {"web"},
        "billing": {"ops"},
    }
