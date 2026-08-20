"""The wire format itself — spec 02's acceptance criteria, as executable tests.

`test_contract_guard.py` proves the *distribution* of schemas cannot drift.
This file proves the *content* is right: that all eleven topics are published
by the service topics.yml names, that every schema is a valid JSON Schema, and
that the example payloads ten other agents will use as fixtures actually
validate against them.

Numbered references are to the acceptance criteria in
docs/specs/02-EVENT-CONTRACTS.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import _registry
import pytest
from _registry import Topic, consumed_dir, load_topics, published_dir
from jsonschema import Draft202012Validator

REGISTRY = load_topics()
TOPICS: list[Topic] = REGISTRY.topics
TOPIC_IDS = [t.name for t in TOPICS]

# Spec 02, "Shared enumerations". Repeated here so a silent edit to a schema
# is caught by a test and not by a service failing in production.
SHARED_ENUMS: dict[str, list[str]] = {
    "authenticity_claim": ["genuine", "oem", "aftermarket", "used", "refurbished", "unknown"],
    "fitment_status": ["compatible", "incompatible", "unknown"],
    "stock_status": ["in_stock", "out_of_stock", "unknown"],
    "provenance": ["rule", "model", "human", "catalog", "consensus"],
    "seller_tier": ["new", "standard", "trusted", "suspended"],
}

ENVELOPE_FIELDS = (
    "event_id",
    "event_type",
    "version",
    "occurred_at",
    "producer",
    "trace_id",
    "payload",
)

# Envelope fields whose sub-schema is identical in all eleven files. event_type
# and producer are legitimately per-topic; the rest are the shared envelope.
INVARIANT_ENVELOPE_FIELDS = ("event_id", "version", "occurred_at", "trace_id")

PERSIAN = re.compile(r"[؀-ۿ]")

# String values that mean an agent typed a placeholder instead of real data.
PLACEHOLDERS = {
    "test",
    "TEST",
    "foo",
    "bar",
    "baz",
    "string",
    "example",
    "todo",
    "TODO",
    "lorem ipsum",
    "changeme",
    "xxx",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_path(topic: Topic) -> Path:
    return published_dir(topic.schema_owner) / topic.schema_filename


def examples_dir(topic: Topic) -> Path:
    return _registry.SERVICES_DIR / topic.schema_owner / "contracts" / "examples" / topic.name


def example_files(topic: Topic) -> list[Path]:
    return sorted(examples_dir(topic).glob("*.json"))


def schema_of(topic: Topic) -> dict[str, Any]:
    loaded: dict[str, Any] = load_json(schema_path(topic))
    return loaded


def validator_of(topic: Topic) -> Any:
    return Draft202012Validator(schema_of(topic))


def payload_schema(topic: Topic) -> dict[str, Any]:
    sub: dict[str, Any] = schema_of(topic)["properties"]["payload"]
    return sub


def first_example(topic: Topic) -> dict[str, Any]:
    files = example_files(topic)
    assert files, topic.name
    loaded: dict[str, Any] = load_json(files[0])
    return loaded


def walk_strings(node: Any) -> list[str]:
    """Every string value anywhere in a document."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in walk_strings(value)]
    if isinstance(node, list):
        return [s for item in node for s in walk_strings(item)]
    return []


def object_schemas(node: Any) -> list[dict[str, Any]]:
    """Every sub-schema that describes an object with declared properties."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "properties" in node and isinstance(node["properties"], dict):
            found.append(node)
        for value in node.values():
            found.extend(object_schemas(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(object_schemas(item))
    return found


def is_nullable(prop_schema: dict[str, Any]) -> bool:
    declared = prop_schema.get("type")
    if isinstance(declared, list):
        return "null" in declared
    if declared == "null":
        return True
    enum = prop_schema.get("enum")
    return isinstance(enum, list) and None in enum


# ---------------------------------------------------------------------------
# Criterion 1 — a schema exists for all eleven topics, owned by the one service
# topics.yml names, and it is a valid JSON Schema.
# ---------------------------------------------------------------------------
def test_all_eleven_topics_are_published() -> None:
    assert len(TOPICS) == 11, "topics.yml no longer declares eleven topics"
    missing = [t.name for t in TOPICS if not schema_path(t).is_file()]
    assert not missing, f"no published schema for: {missing}"


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_schema_is_valid_json_schema(topic: Topic) -> None:
    Draft202012Validator.check_schema(schema_of(topic))


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_only_the_owner_publishes_the_schema(topic: Topic) -> None:
    """The trap: five services produce review.requested, one owns its schema."""
    publishers = [
        service
        for service in _registry.service_dirs()
        if (published_dir(service) / topic.schema_filename).is_file()
    ]
    assert publishers == [topic.schema_owner], (
        f"{topic.name} must be published only by {topic.schema_owner}, found {publishers}"
    )


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_schema_identifies_its_topic(topic: Topic) -> None:
    schema = schema_of(topic)
    assert schema["title"] == topic.name
    event_type = topic.name[len("yadakchi.") : -len(".v1")]
    assert schema["properties"]["event_type"]["const"] == event_type
    assert schema["properties"]["version"]["const"] == 1


# ---------------------------------------------------------------------------
# Criterion 2 — a byte-identical consumed/ copy in every reader. The drift
# guard owns this; here we assert the readers topics.yml declares are the ones
# that actually hold a copy, including the four non-owner producers of
# review.requested.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_every_reader_holds_a_byte_identical_copy(topic: Topic) -> None:
    published = schema_path(topic).read_bytes()
    for service in topic.copy_holders:
        copy = consumed_dir(service) / topic.schema_filename
        assert copy.is_file(), f"{service} reads {topic.name} but has no consumed/ copy"
        assert copy.read_bytes() == published, f"{service} drifted on {topic.name}"


def test_review_requested_has_five_producers_and_one_owner() -> None:
    topic = REGISTRY.by_name("yadakchi.review.requested.v1")
    assert topic is not None
    assert topic.schema_owner == "matcher"
    assert set(topic.producers) == {"matcher", "crawler", "fitment", "enricher", "billing"}
    for producer in topic.other_producers:
        assert not (published_dir(producer) / topic.schema_filename).is_file(), (
            f"{producer} produces review.requested but must never publish its schema"
        )
        assert (consumed_dir(producer) / topic.schema_filename).is_file()


# ---------------------------------------------------------------------------
# The shared envelope. There is no shared code package, so the envelope is
# repeated in all eleven schemas — which is only safe if it is really identical.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_envelope_shape_is_the_same_everywhere(topic: Topic) -> None:
    schema = schema_of(topic)
    assert list(schema["properties"]) == list(ENVELOPE_FIELDS)
    assert schema["required"] == list(ENVELOPE_FIELDS)
    assert schema["type"] == "object"

    reference = schema_of(TOPICS[0])
    for field in INVARIANT_ENVELOPE_FIELDS:
        assert schema["properties"][field] == reference["properties"][field], (
            f"{topic.name} has its own {field} definition — the envelope must be shared"
        )


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_producer_is_constrained_to_the_registry(topic: Topic) -> None:
    declared = schema_of(topic)["properties"]["producer"]["enum"]
    assert declared == list(topic.producers)


# ---------------------------------------------------------------------------
# Criterion 7 — additionalProperties: true at the payload level, and required
# declared for every non-nullable field.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_payload_accepts_unknown_fields(topic: Topic) -> None:
    assert payload_schema(topic)["additionalProperties"] is True
    assert schema_of(topic)["additionalProperties"] is True


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_non_nullable_fields_are_required(topic: Topic) -> None:
    for sub in object_schemas(payload_schema(topic)):
        required = set(sub.get("required", []))
        for name, prop in sub["properties"].items():
            if not is_nullable(prop):
                assert name in required, (
                    f"{topic.name}: '{name}' is not nullable but is not in required"
                )


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_shared_enumerations_are_verbatim(topic: Topic) -> None:
    """Spec 02 says the shared enums are repeated verbatim. Prove nobody edited one."""
    text = schema_path(topic).read_text(encoding="utf-8")
    schema = json.loads(text)
    for sub in object_schemas(schema):
        for name, prop in sub["properties"].items():
            expected = SHARED_ENUMS.get(name)
            if expected is not None and "enum" in prop:
                assert prop["enum"] == expected, f"{topic.name}: '{name}' enum was edited"
    # status and authenticity_dominant carry shared enums under a local name.
    for sub in object_schemas(schema):
        status = sub["properties"].get("status")
        if status is not None and "enum" in status:
            assert status["enum"] == SHARED_ENUMS["fitment_status"]
        dominant = sub["properties"].get("authenticity_dominant")
        if dominant is not None and "enum" in dominant:
            assert dominant["enum"] == SHARED_ENUMS["authenticity_claim"]
        tier = sub["properties"].get("tier")
        if tier is not None and "enum" in tier:
            assert tier["enum"] == SHARED_ENUMS["seller_tier"]


# ---------------------------------------------------------------------------
# Criteria 1 and 3 — at least three realistic examples per topic, and they
# validate.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_at_least_three_examples_exist(topic: Topic) -> None:
    files = example_files(topic)
    assert len(files) >= 3, f"{topic.name} has {len(files)} examples, needs at least three"


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_every_example_validates(topic: Topic) -> None:
    validator = validator_of(topic)
    for path in example_files(topic):
        document = load_json(path)
        errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
        assert not errors, f"{path.name}: " + "; ".join(
            f"{list(e.absolute_path)} {e.message}" for e in errors[:3]
        )


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_examples_use_no_placeholder_values(topic: Topic) -> None:
    """Spec 02, criterion 3: these are fixtures for ten other agents, so a lazy
    "test" string costs everyone downstream."""
    for path in example_files(topic):
        for value in walk_strings(load_json(path)):
            assert value.strip() not in PLACEHOLDERS, f"{path.name}: placeholder value {value!r}"


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_examples_carry_real_persian_data(topic: Topic) -> None:
    """Spec 02, criterion 3: real Persian spare-parts data.

    Three payloads carry no human-readable text at all — every field is an
    identifier, a number or an enum — so there is nowhere for Persian to go.
    They are named rather than skipped by a heuristic, so that adding a text
    field to one of them is a deliberate act.
    """
    identifiers_only = {
        "yadakchi.crossrefs.changed.v1",  # two part codes and canonical brand keys
        "yadakchi.clusters.changed.v1",  # uids, confidences, provenance
        "yadakchi.clicks.recorded.v1",  # uids, a cost, a boolean
    }
    persian_seen = any(
        PERSIAN.search(value)
        for path in example_files(topic)
        for value in walk_strings(load_json(path))
    )
    if topic.name in identifiers_only:
        assert not persian_seen, (
            f"{topic.name} now carries Persian text — take it out of identifiers_only"
        )
    else:
        assert persian_seen, f"{topic.name}: no Persian text in any example"


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_examples_agree_with_the_registry(topic: Topic) -> None:
    event_type = topic.name[len("yadakchi.") : -len(".v1")]
    producers_seen: set[str] = set()
    for path in example_files(topic):
        message = load_json(path)
        assert message["event_type"] == event_type, path.name
        assert message["version"] == 1, path.name
        assert message["producer"] in topic.producers, path.name
        producers_seen.add(message["producer"])
    assert producers_seen, topic.name


# ---------------------------------------------------------------------------
# Criterion 4 — an envelope with an unknown extra field still validates.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_unknown_field_is_ignored_not_rejected(topic: Topic) -> None:
    validator = validator_of(topic)
    message = first_example(topic)
    assert validator.is_valid(message)

    message["a_field_from_a_future_producer"] = {"added": "later", "note_fa": "میدان جدید"}
    assert validator.is_valid(message), "an unknown envelope field must be ignored"

    if isinstance(message["payload"], dict):
        message["payload"]["a_payload_field_from_a_future_producer"] = ["بعدا اضافه شد"]
        assert validator.is_valid(message), "an unknown payload field must be ignored"


@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_a_missing_required_field_is_still_rejected(topic: Topic) -> None:
    """Tolerating unknown fields must not mean tolerating a missing one."""
    validator = validator_of(topic)
    message = first_example(topic)
    del message["trace_id"]
    assert not validator.is_valid(message)


# ---------------------------------------------------------------------------
# Criterion 5 — a tombstone validates on compacted topics and is rejected on
# the others.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("topic", TOPICS, ids=TOPIC_IDS)
def test_tombstone_allowed_exactly_on_compacted_topics(topic: Topic) -> None:
    validator = validator_of(topic)
    tombstone = first_example(topic)
    tombstone["payload"] = None

    if topic.is_compacted:
        assert validator.is_valid(tombstone), (
            f"{topic.name} is compacted, so payload: null must validate as a tombstone"
        )
    else:
        assert not validator.is_valid(tombstone), (
            f"{topic.name} is not compacted, so payload: null must be rejected"
        )


def test_a_real_tombstone_example_is_shipped() -> None:
    """fitment tombstones a vehicle on delete — ship the fixture for it."""
    topic = REGISTRY.by_name("yadakchi.vehicles.changed.v1")
    assert topic is not None
    tombstones = [p for p in example_files(topic) if load_json(p)["payload"] is None]
    assert tombstones, "no tombstone example for yadakchi.vehicles.changed.v1"
    validator = validator_of(topic)
    for path in tombstones:
        assert validator.is_valid(load_json(path))


# ---------------------------------------------------------------------------
# Criterion 6 — offer_uid derivation is stable and matches the documented
# formula. The examples are not hand-typed identities: they are derived, so a
# service agent can reproduce them.
# ---------------------------------------------------------------------------
def derive_offer_uid(source_key: str, external_key: str) -> str:
    return hashlib.sha256(f"{source_key}:{external_key}".encode()).hexdigest()[:32]


def test_offer_uid_matches_the_documented_formula() -> None:
    """offer_uid = first 32 hex chars of sha256("{source_key}:{external_key}")."""
    uid = derive_offer_uid("yadakmarket", "p-482913")
    assert uid == hashlib.sha256(b"yadakmarket:p-482913").hexdigest()[:32]
    assert len(uid) == 32
    assert re.fullmatch(r"[0-9a-f]{32}", uid)


def test_offer_uid_derivation_is_stable() -> None:
    assert derive_offer_uid("yadakmarket", "p-482913") == derive_offer_uid(
        "yadakmarket", "p-482913"
    )
    assert derive_offer_uid("yadakmarket", "p-482913") != derive_offer_uid("otoyar", "p-482913")


def test_the_colon_in_the_offer_uid_formula_cannot_be_ambiguous() -> None:
    """The formula concatenates around a colon, so ("a", "b:c") and ("a:b", "c")
    hash identically. The only thing keeping that collision out of the system is
    that source_key is a slug and cannot contain a colon — so the schema has to
    say so, or two different offers can share one offer_uid."""
    assert derive_offer_uid("a", "b:c") == derive_offer_uid("a:b", "c")

    topic = REGISTRY.by_name("yadakchi.offers.enriched.v1")
    assert topic is not None
    source_key = payload_schema(topic)["properties"]["source_key"]
    pattern = re.compile(source_key["pattern"])
    assert not pattern.fullmatch("yadak:market"), "source_key must not admit a colon"
    assert pattern.fullmatch("yadakmarket")


def test_example_offer_uids_are_really_derived() -> None:
    """Every example that carries the inputs must carry the matching uid."""
    checked = 0
    for topic in TOPICS:
        for path in example_files(topic):
            payload = load_json(path)["payload"]
            if not isinstance(payload, dict):
                continue
            keys = ("source_key", "external_key", "offer_uid")
            if all(k in payload and payload[k] is not None for k in keys):
                expected = derive_offer_uid(payload["source_key"], payload["external_key"])
                assert payload["offer_uid"] == expected, path.name
                checked += 1
    assert checked >= 5, "the fixtures should exercise the derivation on real offers"


def test_example_content_hashes_are_really_sha256_of_the_fragment() -> None:
    topic = REGISTRY.by_name("yadakchi.listings.observed.v1")
    assert topic is not None
    for path in example_files(topic):
        payload = load_json(path)["payload"]
        digest = hashlib.sha256(payload["raw_fragment"].encode("utf-8")).hexdigest()
        assert payload["content_hash"] == digest, path.name


def test_the_examples_are_one_connected_dataset() -> None:
    """The same offers travel the whole chain, so an agent can build an
    end-to-end test out of the fixtures rather than inventing new data."""

    def uids(topic_name: str, extract: str) -> set[str]:
        topic = REGISTRY.by_name(topic_name)
        assert topic is not None
        found: set[str] = set()
        for path in example_files(topic):
            payload = load_json(path)["payload"]
            if isinstance(payload, dict) and payload.get(extract):
                found.add(str(payload[extract]))
        return found

    enriched = uids("yadakchi.offers.enriched.v1", "offer_uid")
    fitted = uids("yadakchi.offers.fitted.v1", "offer_uid")
    assert fitted <= enriched, "offers.fitted references offers with no enriched fixture"

    clusters = uids("yadakchi.clusters.changed.v1", "cluster_uid")
    products = uids("yadakchi.products.changed.v1", "product_uid")
    assert products & clusters, "no product adopts a cluster_uid from the matcher fixtures"

    requested = uids("yadakchi.review.requested.v1", "request_uid")
    decided = uids("yadakchi.review.decided.v1", "request_uid")
    assert decided <= requested, "a review decision answers a request with no fixture"
