import hashlib
import json
from pathlib import Path

from jsonschema import FormatChecker, validators

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "contracts/published"
CONSUMED = ROOT / "contracts/consumed"
EXAMPLES = ROOT / "contracts/examples/yadakchi.listings.observed.v2"


def validate(schema: dict[str, object], value: dict[str, object]) -> None:
    validator_class = validators.validator_for(schema)
    validator_class.check_schema(schema)
    validator_class(schema, format_checker=FormatChecker()).validate(value)


def main() -> None:
    published = sorted(path.name for path in PUBLISHED.glob("*.json"))
    if published != ["yadakchi.listings.observed.v2.json"]:
        raise SystemExit(f"crawler must publish exactly listings.observed.v2; found {published}")
    consumed = sorted(path.name for path in CONSUMED.glob("*.json"))
    expected_consumed = [
        "yadakchi.clicks.recorded.v1.json",
        "yadakchi.review.requested.v1.json",
    ]
    if consumed != expected_consumed:
        raise SystemExit(f"unexpected consumed contract set: {consumed}")

    schema_path = PUBLISHED / published[0]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if schema["properties"]["version"]["const"] != 2:
        raise SystemExit("listings.observed.v2 schema must require envelope version 2")

    examples = sorted(EXAMPLES.glob("*.json"))
    if not examples:
        raise SystemExit("listings.observed.v2 requires examples")
    for example_path in examples:
        event = json.loads(example_path.read_text(encoding="utf-8"))
        validate(schema, event)
        payload = event["payload"]
        actual_hash = hashlib.sha256(payload["raw_fragment"].encode()).hexdigest()
        if actual_hash != payload["fragment_hash"]:
            raise SystemExit(f"fragment hash mismatch in {example_path}")


if __name__ == "__main__":
    main()
