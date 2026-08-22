#!/usr/bin/env python3
"""Distribute docs/specs/ into the service folders, and guard against drift.

Every service is built by a different agent working only inside its own
folder. That agent must find its complete instructions there — without
navigating the repository and without reading twelve specs that do not concern
it. So each services/<name>/ carries:

    BRIEF.md   copy of docs/specs/00-PROJECT-BRIEF.md
    SPEC.md    copy of that service's own spec
    README.md  one paragraph pointing at both

docs/specs/ is the source of truth. The copies are committed, so a fresh clone
gives an agent everything with no setup step. `--check` proves the copies still
match, and CI runs it: editing a copy directly must fail the build.

Usage:
    python platform/scripts/sync_specs.py            # distribute (idempotent)
    python platform/scripts/sync_specs.py --check    # fail on drift
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import _registry
from _registry import BRIEF_SPEC, SPEC_MAP, rel

# One line per service: what it is, for the README's opening sentence.
SUMMARY: dict[str, str] = {
    "ai": "FastAPI inference service — field extraction, pair adjudication and "
    "embeddings for the rest of the system. It has no database.",
    "crawler": "Fetches listings from Iranian seller sites and keeps the immutable raw "
    "archive that the entire pipeline can be rebuilt from.",
    "enricher": "Turns raw listings into Offers: Persian normalization, field extraction, "
    "and the minting of `offer_uid`.",
    "fitment": "Owns the vehicle tree, part-to-vehicle mapping and cross-references — "
    'the answer to "does this part fit my car?".',
    "matcher": "Decides which Offers are the same physical product, and mints "
    "`cluster_uid`. This is the heart of the product.",
    "catalog": "Owns canonical Products, sellers and price history, and serves the read "
    "API the website renders from.",
    "search": "Owns the Typesense index and the search query API.",
    "billing": "Owns outbound redirects, CPC accounting and seller wallets.",
    "ops": "Internal console: review queue, seller dashboard, and the human decisions "
    "that override everything computed.",
    "web": "The public Next.js website — server-rendered, because SEO is the primary "
    "acquisition channel.",
}

README_TEMPLATE = """# {service}

{summary}

**Your instructions are in this folder.** [`BRIEF.md`](./BRIEF.md) is the shared
project brief — read it first, every time — and [`SPEC.md`](./SPEC.md) is the
full specification for this service, including its acceptance criteria. Together
they are everything you need: you own `services/{service}/` and nothing else, you
never read or modify another service's folder or database, and you talk to other
services only over Kafka (plus the few synchronous HTTP pairs the brief allows).
If something you need is missing from a spec, stop and report it rather than
inventing it.

Both files are copies, distributed from `docs/specs/` by `make sync-specs`.
**Do not edit them here** — CI compares them byte-for-byte against the source and
will fail the build. Spec changes happen in `docs/specs/`, reviewed by a human.
"""


# Persian normalization is implemented three times over, by services that may
# not import each other's code, and all three must agree exactly or a query
# stops matching text derived from the same words. So the rules and their
# conformance vectors are distributed like a spec, not referred to across a
# boundary nobody is allowed to cross.
NORMALIZATION_SPEC = "13-TEXT-NORMALIZATION.md"
NORMALIZATION_VECTORS = Path("platform") / "text" / "normalization-vectors.json"
NORMALIZES_PERSIAN = ("enricher", "fitment", "search")


def _targets(service: str) -> list[tuple[Path, Path]]:
    """(source, destination in the service folder) pairs."""
    folder = _registry.SERVICES_DIR / service
    targets = [
        (_registry.SPECS_DIR / BRIEF_SPEC, folder / "BRIEF.md"),
        (_registry.SPECS_DIR / SPEC_MAP[service], folder / "SPEC.md"),
    ]
    if service in NORMALIZES_PERSIAN:
        targets += [
            (_registry.SPECS_DIR / NORMALIZATION_SPEC, folder / "NORMALIZATION.md"),
            (_registry.REPO_ROOT / NORMALIZATION_VECTORS, folder / "normalization-vectors.json"),
        ]
    return targets


def distribute() -> int:
    written = 0
    unchanged = 0
    for service in SPEC_MAP:
        folder = _registry.SERVICES_DIR / service
        folder.mkdir(parents=True, exist_ok=True)

        for source, target in _targets(service):
            if not source.is_file():
                print(f"FAIL  missing source spec {rel(source)}", file=sys.stderr)
                return 1
            if target.is_file() and target.read_bytes() == source.read_bytes():
                unchanged += 1
                continue
            shutil.copyfile(source, target)
            print(f"copied {rel(source)} -> {rel(target)}")
            written += 1

        # The README is scaffolding, not a copy: written once, then left alone
        # so the service's own agent can extend it.
        readme = folder / "README.md"
        if not readme.is_file():
            readme.write_text(
                README_TEMPLATE.format(service=service, summary=SUMMARY[service]),
                encoding="utf-8",
            )
            print(f"wrote  {rel(readme)}")
            written += 1

    print(f"specs: updated {written}, already identical {unchanged}")
    return 0


def check() -> int:
    problems: list[str] = []
    checked = 0
    for service in SPEC_MAP:
        for source, target in _targets(service):
            if not source.is_file():
                problems.append(f"missing source spec {rel(source)}")
                continue
            if not target.is_file():
                problems.append(f"{rel(target)} is missing — run `make sync-specs`")
                continue
            if target.read_bytes() != source.read_bytes():
                problems.append(
                    f"{rel(target)} has drifted from {rel(source)}. "
                    "docs/specs/ is the source of truth: revert this copy and run "
                    "`make sync-specs`, or change the spec itself."
                )
                continue
            checked += 1
        if not (_registry.SERVICES_DIR / service / "README.md").is_file():
            problems.append(f"services/{service}/README.md is missing — run `make sync-specs`")

    if problems:
        print("\nspec drift detected:", file=sys.stderr)
        for problem in problems:
            print(f"  FAIL  {problem}", file=sys.stderr)
        print(f"\n{len(problems)} problem(s).", file=sys.stderr)
        return 1

    print(f"specs OK — {checked} distributed copies identical to docs/specs/")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify copies match; write nothing")
    args = parser.parse_args(argv)
    return check() if args.check else distribute()


if __name__ == "__main__":
    raise SystemExit(main())
