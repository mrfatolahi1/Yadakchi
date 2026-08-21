"""Write the read API's OpenAPI document to contracts/published/.

`web` and `ops` are the only synchronous callers this service has, and this
file is the contract they hold us to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from catalog.api import api

DEFAULT_TARGET = Path(__file__).resolve().parents[4] / "contracts" / "published" / "openapi.json"


class Command(BaseCommand):
    help = "Export the OpenAPI schema to contracts/published/openapi.json."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--output", default=str(DEFAULT_TARGET))
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit non-zero if the file on disk is out of date.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        target = Path(options["output"])
        rendered = json.dumps(api.get_openapi_schema(), indent=2, ensure_ascii=False) + "\n"

        if options["check"]:
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            if current != rendered:
                raise SystemExit(
                    f"{target} is out of date — run `make openapi` and commit the result"
                )
            self.stdout.write("openapi.json is up to date")
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        self.stdout.write(f"wrote {target}")
