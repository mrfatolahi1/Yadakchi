from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema.validators import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CONSUMED = ROOT / "contracts" / "consumed"
PUBLISHED = ROOT / "contracts" / "published"

EVENT_CONTRACTS = {
    "yadakchi.products.changed.v1.json",
    "yadakchi.vehicles.changed.v1.json",
    "yadakchi.crossrefs.changed.v1.json",
    "yadakchi.review.decided.v1.json",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def main() -> None:
    actual_event_contracts = {path.name for path in CONSUMED.glob("yadakchi.*.json")}
    if actual_event_contracts != EVENT_CONTRACTS:
        raise SystemExit("consumed event contract set does not match SPEC.md")
    for filename in sorted(EVENT_CONTRACTS):
        Draft202012Validator.check_schema(load_json(CONSUMED / filename))

    ai_openapi = load_json(CONSUMED / "ai-openapi.json")
    if "/v1/embed" not in ai_openapi.get("paths", {}):
        raise SystemExit("AI OpenAPI does not define POST /v1/embed")
    embed_schema = ai_openapi.get("components", {}).get("schemas", {}).get("EmbedResponse", {})
    if embed_schema.get("properties", {}).get("dim", {}).get("const") != 384:
        raise SystemExit("AI embedding contract dimension is not 384")

    vectors = load_json(ROOT / "normalization-vectors.json")
    if not vectors.get("normalize_text") or not vectors.get("normalize_part_number"):
        raise SystemExit("normalization conformance vectors are empty")

    published_files = {path.name for path in PUBLISHED.iterdir() if path.name != ".gitkeep"}
    if published_files != {"openapi.json"}:
        raise SystemExit("search may publish only contracts/published/openapi.json")
    search_openapi = load_json(PUBLISHED / "openapi.json")
    required_paths = {
        "/v1/search",
        "/v1/suggest",
        "/v1/events/click",
        "/v1/health",
        "/metrics",
    }
    if not required_paths.issubset(search_openapi.get("paths", {})):
        raise SystemExit("published search OpenAPI is missing required paths")


if __name__ == "__main__":
    main()
