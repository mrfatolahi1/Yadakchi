from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = {
    "yadakchi.crossrefs.changed.v1.json",
    "yadakchi.offers.fitted.v1.json",
    "yadakchi.vehicles.changed.v1.json",
}
CONSUMED = {
    "yadakchi.offers.enriched.v1.json",
    "yadakchi.review.decided.v1.json",
    "yadakchi.review.requested.v1.json",
}


def load(path: Path) -> object:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def main() -> None:
    published_dir = ROOT / "contracts" / "published"
    consumed_dir = ROOT / "contracts" / "consumed"
    actual_published = {path.name for path in published_dir.glob("*.json")}
    actual_consumed = {path.name for path in consumed_dir.glob("*.json")}
    if actual_published != PUBLISHED:
        raise SystemExit(f"published ownership drift: {sorted(actual_published)}")
    if actual_consumed != CONSUMED:
        raise SystemExit(f"consumed contract drift: {sorted(actual_consumed)}")
    if "yadakchi.review.requested.v1.json" in actual_published:
        raise SystemExit("review.requested is owned by matcher and must not be published here")

    schemas = {path.stem: load(path) for path in published_dir.glob("*.json")}
    for schema in [*schemas.values(), *(load(path) for path in consumed_dir.glob("*.json"))]:
        Draft202012Validator.check_schema(schema)

    for topic_dir in (ROOT / "contracts" / "examples").iterdir():
        if not topic_dir.is_dir():
            continue
        schema = schemas.get(topic_dir.name)
        if schema is None:
            raise SystemExit(f"example directory has no owned schema: {topic_dir.name}")
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for example in topic_dir.glob("*.json"):
            validator.validate(load(example))


if __name__ == "__main__":
    main()
