#!/usr/bin/env python3
"""Copy every published event schema into the folder of every service that
reads it, as declared in platform/kafka/topics.yml.

This is the one command that resolves contract drift after a deliberate change:
edit the schema in the owning service, run `make sync-contracts`, commit the
result. Never edit a consumed/ copy — `make check-contracts` will reject it.

Idempotent: running it twice changes nothing.

Usage:
    python platform/scripts/sync_contracts.py [--dry-run]
"""

from __future__ import annotations

import argparse
import shutil
import sys

from _registry import consumed_dir, load_topics, published_dir, rel, service_dirs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    args = parser.parse_args(argv)

    registry = load_topics()
    services = service_dirs()
    copied = 0
    unchanged = 0
    pending: list[str] = []

    for topic in registry.topics:
        source = published_dir(topic.schema_owner) / topic.schema_filename
        if not source.is_file():
            pending.append(topic.name)
            continue
        payload = source.read_bytes()

        for service in topic.copy_holders:
            if service not in services:
                print(f"WARN  no services/{service}/ folder for {topic.name}", file=sys.stderr)
                continue
            target = consumed_dir(service) / topic.schema_filename
            if target.is_file() and target.read_bytes() == payload:
                unchanged += 1
                continue
            if args.dry_run:
                print(f"would copy {rel(source)} -> {rel(target)}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                print(f"copied {rel(source)} -> {rel(target)}")
            copied += 1

    verb = "would update" if args.dry_run else "updated"
    print(f"contracts: {verb} {copied}, already identical {unchanged}")
    if pending:
        print(f"  (no schema published yet: {', '.join(pending)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
