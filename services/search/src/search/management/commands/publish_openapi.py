from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from search.api import api


class Command(BaseCommand):
    help = "Publish or verify the search service OpenAPI document."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--check", action="store_true")

    def handle(self, *args: object, **options: Any) -> None:
        del args
        target = Path(settings.BASE_DIR) / "contracts" / "published" / "openapi.json"
        rendered = json.dumps(api.get_openapi_schema(), ensure_ascii=False, indent=2) + "\n"
        if options["check"]:
            if not target.exists() or target.read_text() != rendered:
                raise CommandError("contracts/published/openapi.json is stale")
            return
        target.write_text(rendered)
        self.stdout.write(self.style.SUCCESS(str(target)))
