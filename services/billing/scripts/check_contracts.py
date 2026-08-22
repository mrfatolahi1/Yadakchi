from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def main() -> None:
    consumed = ROOT / "contracts" / "consumed"
    published = ROOT / "contracts" / "published"
    required_consumed = {
        "yadakchi.sellers.changed.v1.json",
        "yadakchi.review.requested.v1.json",
    }
    required_published = {
        "yadakchi.clicks.recorded.v1.json",
        "yadakchi.seller_billing.changed.v1.json",
        "openapi.json",
    }
    missing = [
        str(path)
        for path in [
            *(consumed / name for name in required_consumed),
            *(published / name for name in required_published),
        ]
        if not path.exists()
    ]
    if missing:
        raise SystemExit(f"missing contracts: {', '.join(missing)}")
    if (published / "yadakchi.review.requested.v1.json").exists():
        raise SystemExit("billing must not publish the matcher-owned review.requested schema")

    schemas: dict[str, dict[str, Any]] = {}
    paths = (*consumed.glob("*.json"), *published.glob("yadakchi.*.json"))
    for path in sorted(paths):
        schema = load(path)
        Draft202012Validator.check_schema(schema)
        schemas[path.name.removesuffix(".json")] = schema

    for topic_dir in (ROOT / "contracts" / "examples").iterdir():
        if not topic_dir.is_dir():
            continue
        validator = Draft202012Validator(schemas[topic_dir.name], format_checker=FormatChecker())
        for example in topic_dir.glob("*.json"):
            validator.validate(load(example))


if __name__ == "__main__":
    main()
