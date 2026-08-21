"""The published contract must match the live schema.

`enricher`, `matcher` and `search` vendor `contracts/published/openapi.json`
and generate clients from it. Nobody reads this service's code — they read
that file — so a schema change that is not committed is a silent lie to three
other services. This test is the guard.

Regenerate with `make openapi` after any deliberate change.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from ai.export_openapi import CONTRACT_PATH, dumps, openapi_document


def published() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return document


def test_the_committed_document_matches_the_live_schema(client: TestClient) -> None:
    """Acceptance criterion 9."""
    live = client.get("/openapi.json").json()

    assert published() == live, (
        "contracts/published/openapi.json is out of date — run `make openapi` and commit it"
    )


def test_the_committed_file_is_byte_identical_to_the_exporter() -> None:
    assert CONTRACT_PATH.read_text(encoding="utf-8") == dumps()


def test_the_document_declares_the_three_operations() -> None:
    document = published()
    assert set(document["paths"]) == {
        "/v1/extract",
        "/v1/judge",
        "/v1/embed",
        "/health",
        "/metrics",
    }
    operation_ids = {
        method["operationId"] for path in document["paths"].values() for method in path.values()
    }
    assert operation_ids == {"extract", "judge", "embed", "health", "metrics"}


def test_the_error_body_and_the_budget_code_are_documented() -> None:
    document = published()
    schemas = document["components"]["schemas"]

    assert set(schemas["ErrorResponse"]["properties"]) == {"code", "message", "detail"}
    for path in ("/v1/extract", "/v1/judge", "/v1/embed"):
        responses = document["paths"][path]["post"]["responses"]
        assert "429" in responses
        assert "budget_exhausted" in responses["429"]["description"]
        assert (
            responses["429"]["content"]["application/json"]["schema"]["$ref"]
            == "#/components/schemas/ErrorResponse"
        )


def test_the_384_dimension_contract_is_visible_in_the_schema() -> None:
    schemas = published()["components"]["schemas"]

    assert schemas["EmbedResponse"]["properties"]["dim"]["const"] == 384
    assert schemas["EmbedRequest"]["properties"]["texts"]["maxItems"] == 256


def test_the_document_is_deterministic() -> None:
    assert openapi_document() == openapi_document()
