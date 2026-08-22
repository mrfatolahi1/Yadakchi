from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from billing.api import api


class Command(BaseCommand):
    help = "Export the owned OpenAPI contract"

    def handle(self, *args: Any, **options: Any) -> None:
        destination = settings.BASE_DIR / "contracts" / "published" / "openapi.json"
        destination.write_text(
            json.dumps(api.get_openapi_schema(), indent=2, sort_keys=True) + "\n"
        )
