#!/usr/bin/env python3
"""Decide which CI jobs a change needs to run.

CI is path-filtered: a change under services/matcher/ runs the matcher job and
nothing else. That keeps a ten-service monorepo's feedback loop short, and it
keeps one agent's broken branch from failing nine unrelated jobs.

The rules, in order:

  * a change to the platform, the root files or CI itself  -> run everything,
    because those files define how every service is built and run;
  * a change under docs/specs/ -> run everything, because specs are
    distributed into every service folder;
  * a change under services/<name>/ -> run only that service;
  * anything else (README, docs) -> run only the always-on guards.

The contract and spec guards always run. They are the checks that catch two
services disagreeing, which is exactly the failure a path filter could hide.

Usage:
    python platform/scripts/ci_matrix.py --changed-from origin/master
    git diff --name-only HEAD~1 | python platform/scripts/ci_matrix.py
    python platform/scripts/ci_matrix.py services/matcher/models.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

from _registry import SERVICES

PYTHON_SERVICES: tuple[str, ...] = tuple(s for s in SERVICES if s != "web")

# A change to any of these means every service could be affected.
GLOBAL_PREFIXES = (
    "platform/",
    ".github/",
    "docs/specs/",
)
GLOBAL_FILES = (
    "Makefile",
    "docker-compose.yml",
    "pyproject.toml",
    ".dockerignore",
)


def _changed_from_git(base: str) -> list[str]:
    merge_base = subprocess.run(  # noqa: S603
        ["git", "merge-base", base, "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    ref = merge_base.stdout.strip() or base
    result = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-only", f"{ref}...HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def is_global(path: str) -> bool:
    return path.startswith(GLOBAL_PREFIXES) or path in GLOBAL_FILES


def service_of(path: str) -> str | None:
    parts = Path(path).parts
    if len(parts) >= 2 and parts[0] == "services" and parts[1] in SERVICES:
        return parts[1]
    return None


class Plan(TypedDict):
    """Which jobs a change needs. Mirrors the outputs of the `changes` CI job."""

    python_services: list[str]
    web: bool
    any_service: bool
    reason: str


def plan(changed: list[str]) -> Plan:
    """Which jobs this change needs. Contract and spec guards are unconditional."""
    if any(is_global(path) for path in changed):
        selected = list(SERVICES)
        reason = "platform, CI or spec change — every service is affected"
    else:
        touched = {s for path in changed if (s := service_of(path)) is not None}
        selected = [s for s in SERVICES if s in touched]
        reason = (
            f"changed services: {', '.join(selected)}" if selected else "no service code changed"
        )

    return Plan(
        python_services=[s for s in selected if s in PYTHON_SERVICES],
        web="web" in selected,
        any_service=bool(selected),
        reason=reason,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="changed paths; omit to read stdin")
    parser.add_argument("--changed-from", help="git ref to diff against, e.g. origin/master")
    parser.add_argument(
        "--github-output", action="store_true", help="also append to $GITHUB_OUTPUT"
    )
    args = parser.parse_args(argv)

    if args.changed_from:
        changed = _changed_from_git(args.changed_from)
    elif args.paths:
        changed = args.paths
    else:
        changed = [line.strip() for line in sys.stdin if line.strip()]

    result = plan(changed)
    print(f"{len(changed)} changed path(s): {result['reason']}", file=sys.stderr)
    print(json.dumps(result, indent=2))

    if args.github_output:
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a", encoding="utf-8") as handle:
                handle.write(f"python_services={json.dumps(result['python_services'])}\n")
                handle.write(f"web={str(result['web']).lower()}\n")
                handle.write(f"any_service={str(result['any_service']).lower()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
