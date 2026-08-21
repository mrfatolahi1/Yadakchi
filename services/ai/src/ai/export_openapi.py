"""Write the published OpenAPI contract.

    python -m ai.export_openapi            # writes contracts/published/openapi.json
    python -m ai.export_openapi --stdout

`enricher`, `matcher` and `search` vendor that file and generate clients from
it, so it is regenerated deliberately and reviewed like any other contract
change. `tests/test_openapi_contract.py` fails the build if the committed copy
and the live schema ever disagree.

The document is generated from a pinned stub configuration so that it depends
on nothing in the environment: the same commit always produces the same bytes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ai.config import Backend, EmbedBackend, Settings
from ai.main import create_app

#: services/ai/contracts/published/openapi.json
CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "published" / "openapi.json"


def export_settings() -> Settings:
    """A configuration that cannot vary between machines."""
    return Settings(
        ai_backend=Backend.STUB,
        ai_embed_backend=EmbedBackend.STUB,
        ai_budget_enabled=True,
        ai_daily_budget=3600.0,
        redis_url=None,
    )


def openapi_document() -> dict[str, Any]:
    app = create_app(export_settings())
    document: dict[str, Any] = app.openapi()
    return document


def dumps() -> str:
    """Exactly what is committed: UTF-8, two-space indent, trailing newline."""
    return json.dumps(openapi_document(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdout", action="store_true", help="print instead of writing the file")
    args = parser.parse_args(argv)

    document = dumps()
    if args.stdout:
        sys.stdout.write(document)
        return 0
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(document, encoding="utf-8")
    print(f"wrote {CONTRACT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
