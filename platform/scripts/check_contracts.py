#!/usr/bin/env python3
"""Contract drift guard — the thing standing between ten agents and chaos.

Ten services are written by ten agents that never read each other's code. The
only thing keeping them agreeing on the wire format is a JSON Schema file per
topic, owned by one service and copied byte-for-byte into everyone who reads
it. This script proves that agreement still holds.

It checks, against platform/kafka/topics.yml:

  1. Every consumed/ copy is byte-identical to the publisher's published/ file.
  2. Every topic has exactly one publisher of its schema.
  3. No declared consumer is missing a consumed/ copy, and no service holds a
     consumed/ copy of a topic it was never declared to read.
  4. No stray schema files for topics that do not exist in the registry.

And the same three things for HTTP, against platform/http/apis.yml: every
caller of an API holds a byte-identical <publisher>-openapi.json, no declared
caller is missing one, and nobody vendors a document they never declared. A
caller cannot fetch the document itself — reading another service's directory
is forbidden — so `make sync-contracts` puts it there and this proves it is
still current.

Exit code is non-zero on any mismatch, and every message names the exact
service and topic so the fix is obvious: change the source, run
`make sync-contracts`, commit.

A NOTE ON THE EMPTY SKELETON: before spec 02 lands, no service has published
anything. That is a valid state and passes. A topic becomes enforced the
moment its owner publishes a schema.

Usage:
    python platform/scripts/check_contracts.py [--verbose]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _registry import (
    Registry,
    Topic,
    consumed_dir,
    load_apis,
    load_topics,
    published_dir,
    rel,
    service_dirs,
    vendored_openapi_names,
)


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _check_registry_sanity(registry: Registry, services: list[str], errors: list[str]) -> None:
    """topics.yml itself must be coherent before anything else is meaningful."""
    known = set(services)
    seen: set[str] = set()
    for topic in registry.topics:
        if topic.name in seen:
            errors.append(f"{topic.name}: declared twice in topics.yml")
        seen.add(topic.name)

        if topic.schema_owner not in topic.producers:
            errors.append(
                f"{topic.name}: schema_owner '{topic.schema_owner}' is not listed in producers"
            )
        for role, names in (("producer", topic.producers), ("consumer", topic.consumers)):
            for name in names:
                if name not in known:
                    errors.append(
                        f"{topic.name}: {role} '{name}' is not a service folder under services/"
                    )
        overlap = set(topic.producers) & set(topic.consumers)
        if overlap:
            errors.append(
                f"{topic.name}: {sorted(overlap)} both produces and consumes it — "
                "a service must not feed itself through Kafka"
            )
        if topic.cleanup not in {"delete", "compact"}:
            errors.append(f"{topic.name}: cleanup must be 'delete' or 'compact'")
        if topic.cleanup == "compact" and topic.dlq:
            errors.append(f"{topic.name}: compacted topics take no .dlq companion")


def _publishers_of(topic: Topic, services: list[str]) -> list[str]:
    return [s for s in services if (published_dir(s) / topic.schema_filename).is_file()]


def _check_publishers(
    registry: Registry, services: list[str], errors: list[str], verbose: bool
) -> dict[str, Path]:
    """Exactly one service may own each topic's schema. Returns owner paths."""
    owned: dict[str, Path] = {}
    for topic in registry.topics:
        publishers = _publishers_of(topic, services)

        if len(publishers) > 1:
            errors.append(
                f"{topic.name}: {len(publishers)} publishers "
                f"({', '.join(publishers)}) — exactly one service owns a schema. "
                f"topics.yml says '{topic.schema_owner}'."
            )
            continue

        if not publishers:
            if verbose:
                print(f"skip  {topic.name}: not published yet (owner: {topic.schema_owner})")
            continue

        (publisher,) = publishers
        if publisher != topic.schema_owner:
            errors.append(
                f"{topic.name}: published by '{publisher}' but topics.yml assigns "
                f"the schema to '{topic.schema_owner}'"
            )
            continue

        path = published_dir(publisher) / topic.schema_filename
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(f"{topic.name}: {rel(path)} is not valid JSON ({exc})")
            continue
        owned[topic.name] = path

    return owned


def _check_copies(
    registry: Registry,
    owned: dict[str, Path],
    errors: list[str],
    verbose: bool,
) -> int:
    """Every declared reader must hold a byte-identical copy."""
    verified = 0
    for topic in registry.topics:
        source = owned.get(topic.name)
        if source is None:
            continue
        expected = _read(source)
        for service in topic.copy_holders:
            copy = consumed_dir(service) / topic.schema_filename
            if not copy.is_file():
                errors.append(
                    f"{service} consumes {topic.name} but has no "
                    f"{rel(copy)} — run `make sync-contracts`"
                )
                continue
            if _read(copy) != expected:
                errors.append(
                    f"{service} has drifted from {topic.schema_owner} on {topic.name}: "
                    f"{rel(copy)} differs from {rel(source)}. "
                    "The published file is the source of truth — run `make sync-contracts`."
                )
                continue
            verified += 1
            if verbose:
                print(f"ok    {service}/consumed/{topic.schema_filename}")
    return verified


def _is_openapi_document(path: Path) -> bool:
    """OpenAPI documents share the contracts/ folders but are not event schemas.

    They must not be mistaken for an invented topic by the stray check; they are
    checked against platform/http/apis.yml instead, by _check_http_apis below.
    """
    return path.stem == "openapi" or path.stem.endswith("-openapi")


def _check_http_apis(services: list[str], errors: list[str], verbose: bool) -> int:
    """Every vendored OpenAPI copy is byte-identical to its publisher's.

    Same contract as a Kafka schema, and for the same reason: a caller cannot
    read the publisher's directory, so it holds a copy, and a copy drifts unless
    something compares it. A publisher that has not shipped its document yet is
    a valid state.
    """
    apis = load_apis()
    known = set(services)
    verified = 0

    for api in apis:
        if api.publisher not in known:
            errors.append(f"apis.yml: publisher '{api.publisher}' is not a service folder")
            continue
        if api.publisher in api.consumers:
            errors.append(f"apis.yml: {api.publisher} lists itself as a consumer of its own API")
        for consumer in api.consumers:
            if consumer not in known:
                errors.append(
                    f"apis.yml: consumer '{consumer}' of {api.publisher} is not a service folder"
                )

        source = published_dir(api.publisher) / api.published_filename
        if not source.is_file():
            if verbose:
                print(f"skip  {api.publisher} openapi: not published yet")
            continue

        document = source.read_bytes()
        for consumer in api.consumers:
            if consumer not in known:
                continue
            copy = consumed_dir(consumer) / api.vendored_filename
            if not copy.is_file():
                errors.append(
                    f"{consumer} calls {api.publisher} over HTTP but has no "
                    f"{rel(copy)} — run `make sync-contracts`"
                )
                continue
            if copy.read_bytes() != document:
                errors.append(
                    f"{consumer} has drifted from {api.publisher} on its OpenAPI document: "
                    f"{rel(copy)} differs from {rel(source)}. "
                    "The published file is the source of truth — run `make sync-contracts`."
                )
                continue
            verified += 1
            if verbose:
                print(f"ok    {consumer}/consumed/{api.vendored_filename}")

    # A vendored document nobody declared is the HTTP version of an undeclared
    # topic dependency, and is caught the same way.
    legal = vendored_openapi_names()
    allowed_by_consumer = {
        consumer: {a.vendored_filename for a in apis if consumer in a.consumers}
        for consumer in services
    }
    for service in services:
        for path in sorted(consumed_dir(service).glob("*-openapi.json")):
            if path.name not in legal:
                errors.append(
                    f"{service} keeps {rel(path)} but no service publishes it — "
                    "declare the dependency in platform/http/apis.yml"
                )
            elif path.name not in allowed_by_consumer[service]:
                errors.append(
                    f"{service} keeps {rel(path)} but is not declared as a caller of it "
                    "in platform/http/apis.yml — declare the dependency or delete the copy"
                )
    return verified


def _check_strays(registry: Registry, services: list[str], errors: list[str]) -> None:
    """No schema may exist for a topic nobody declared, or in a service that
    was never declared to read it. This is how an agent quietly inventing a
    topic gets caught."""
    for service in services:
        for path in sorted(published_dir(service).glob("*.json")):
            if _is_openapi_document(path):
                continue
            topic = registry.by_name(path.stem)
            if topic is None:
                errors.append(
                    f"{service} publishes {rel(path)} but '{path.stem}' is not in "
                    "platform/kafka/topics.yml — declare the topic first"
                )
        for path in sorted(consumed_dir(service).glob("*.json")):
            if _is_openapi_document(path):
                continue
            topic = registry.by_name(path.stem)
            if topic is None:
                errors.append(
                    f"{service} keeps {rel(path)} but '{path.stem}' is not in "
                    "platform/kafka/topics.yml"
                )
            elif service not in topic.copy_holders:
                errors.append(
                    f"{service} keeps a copy of {topic.name} but is not declared as a "
                    "consumer of it in topics.yml — declare the dependency or delete the copy"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", "-v", action="store_true", help="list every verified file")
    args = parser.parse_args(argv)

    registry = load_topics()
    services = service_dirs()
    errors: list[str] = []

    _check_registry_sanity(registry, services, errors)
    owned = _check_publishers(registry, services, errors, args.verbose)
    verified = _check_copies(registry, owned, errors, args.verbose)
    verified += _check_http_apis(services, errors, args.verbose)
    _check_strays(registry, services, errors)

    if errors:
        print("", file=sys.stderr)
        print("contract drift detected:", file=sys.stderr)
        for error in errors:
            print(f"  FAIL  {error}", file=sys.stderr)
        print(f"\n{len(errors)} problem(s). Nothing was modified.", file=sys.stderr)
        return 1

    published = len(owned)
    total = len(registry.topics)
    print(
        f"contracts OK — {published}/{total} topics published, "
        f"{verified} consumer copies verified, {len(services)} services checked"
    )
    if published < total:
        pending = sorted(t.name for t in registry.topics if t.name not in owned)
        print(f"  (not published yet: {', '.join(pending)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
