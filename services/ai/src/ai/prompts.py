"""Prompt loading, versioning, and the guard that keeps the two in step.

`PROMPT_VERSION` is part of every cache key. If a prompt changes and the
version does not, thirty days of cached answers keep being served from a prompt
that no longer exists — so the fingerprint below is checked by a test, and
editing a prompt without bumping the version fails the build.

The fingerprint covers the registered schemas too, because the schema is what
the prompt promises to fill.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Final

from ai.schemas import SCHEMA_REGISTRY

#: Bump on any change to prompts/*.txt or to a registered schema. Bumping
#: invalidates every cached answer, which is the point.
PROMPT_VERSION: Final[str] = "v1"

#: sha256 over the prompt files and the registered JSON schemas. Regenerate
#: with `make prompt-fingerprint` after a deliberate change — and bump
#: PROMPT_VERSION in the same commit.
PROMPT_FINGERPRINT: Final[str] = "c2e2d29fe5e93cc2178cce74582b297908adf7347861f89cf2778a29d617cbef"

PROMPT_DIR: Final[Path] = Path(__file__).parent / "prompts"

SYSTEM_PROMPT_FILE: Final[str] = "system.txt"
JUDGE_PROMPT_FILE: Final[str] = "judge_same_part.txt"


@cache
def load_prompt(filename: str) -> str:
    """Read a prompt from disk once. Prompts are immutable at runtime."""
    path = PROMPT_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def render(template: str, **values: str) -> str:
    """Fill `{{NAME}}` placeholders.

    Deliberately not str.format: the prompts are full of JSON braces, and a
    templating language that trips over `{"fields": ...}` has no place here.
    """
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key.upper() + "}}", value)
    return text


def system_prompt() -> str:
    return load_prompt(SYSTEM_PROMPT_FILE)


def compute_fingerprint() -> str:
    """Digest of everything a prompt version is supposed to pin."""
    digest = hashlib.sha256()
    for path in sorted(PROMPT_DIR.glob("*.txt")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(path.read_bytes())
        digest.update(b"\x1e")
    for name in sorted(SCHEMA_REGISTRY):
        schema = SCHEMA_REGISTRY[name].model.model_json_schema()
        digest.update(name.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(json.dumps(schema, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def prompt_files() -> list[str]:
    return sorted(path.name for path in PROMPT_DIR.glob("*.txt"))


def _rewrite_fingerprint() -> int:
    """`make prompt-fingerprint` — stamp the current digest into this file.

    Deliberately a separate, explicit step: it is the moment to ask yourself
    whether PROMPT_VERSION should move too.
    """
    import re

    source = Path(__file__)
    current = compute_fingerprint()
    text = source.read_text(encoding="utf-8")
    updated = re.sub(
        r'PROMPT_FINGERPRINT: Final\[str\] = "[0-9a-f]{64}"',
        f'PROMPT_FINGERPRINT: Final[str] = "{current}"',
        text,
        count=1,
    )
    if updated == text:
        print(f"fingerprint unchanged: {current}")
        return 0
    source.write_text(updated, encoding="utf-8")
    print(f"fingerprint updated to {current} — bump PROMPT_VERSION if the prompts changed")
    return 0


if __name__ == "__main__":
    import sys

    if "--write" in sys.argv:
        raise SystemExit(_rewrite_fingerprint())
    print(compute_fingerprint())
