from __future__ import annotations

from typing import Any

import httpx
import pytest

from search.query_builder import SearchFilters
from search.typesense_client import TypesenseClient


def test_vector_search_uses_post_multi_search_for_large_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured.update(method=method, url=url, **kwargs)
        return httpx.Response(
            200,
            json={"results": [{"hits": []}]},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", request)
    client = TypesenseClient("http://typesense:8108", "test-key")

    assert client.vector_search([0.0] * 384, SearchFilters(), None, 25) == []
    assert captured["method"] == "POST"
    assert captured["url"] == "http://typesense:8108/multi_search"
    search = captured["json"]["searches"][0]
    assert search["collection"] == "products"
    assert search["vector_query"].startswith("embedding:([")
