from __future__ import annotations

import json
import os
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing.settings")
django.setup()

from billing.api import api  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    published = json.loads((ROOT / "contracts" / "published" / "openapi.json").read_text())
    generated = json.loads(json.dumps(api.get_openapi_schema()))
    if published != generated:
        raise SystemExit("contracts/published/openapi.json is stale; run make openapi")


if __name__ == "__main__":
    main()
