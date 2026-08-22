from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

import httpx

from search.query_builder import SearchFilters, build_filter_by


class TypesenseError(RuntimeError):
    pass


class SearchIndex(Protocol):
    def ensure_collection(self) -> None: ...

    def reset_collection(self) -> None: ...

    def upsert(self, document: dict[str, Any]) -> None: ...

    def delete(self, product_uid: str) -> None: ...

    def exact_search(
        self,
        part_number: str,
        filters: SearchFilters,
        vehicle_slug: str | None,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def lexical_search(
        self,
        query: str,
        filters: SearchFilters,
        vehicle_slug: str | None,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def vector_search(
        self,
        vector: list[float],
        filters: SearchFilters,
        vehicle_slug: str | None,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def suggest(self, query: str, limit: int) -> list[dict[str, Any]]: ...

    def upsert_synonym(self, part_type: str, tokens: list[str]) -> None: ...

    def delete_synonym(self, part_type: str) -> None: ...

    def health(self) -> bool: ...


COLLECTION_SCHEMA: dict[str, Any] = {
    "name": "products",
    "enable_nested_fields": False,
    "fields": [
        {"name": "product_uid", "type": "string"},
        {"name": "title", "type": "string"},
        {"name": "title_variants", "type": "string[]"},
        {"name": "brand", "type": "string", "facet": True, "optional": True},
        {"name": "part_type", "type": "string", "facet": True, "optional": True},
        {"name": "part_type_synonyms", "type": "string[]"},
        {"name": "part_numbers", "type": "string[]", "facet": True},
        {"name": "vehicle_compatible", "type": "string[]", "facet": True},
        {"name": "vehicle_incompatible", "type": "string[]", "facet": True},
        {"name": "authenticity_dominant", "type": "string", "facet": True},
        {"name": "min_price_toman", "type": "int64", "facet": True, "optional": True},
        {"name": "offer_count", "type": "int32"},
        {"name": "has_image", "type": "bool", "facet": True},
        {"name": "embedding", "type": "float[]", "num_dim": 384},
        {"name": "updated_at", "type": "int64"},
        {"name": "price_freshness", "type": "int64"},
        {"name": "best_seller_trust", "type": "float"},
        {"name": "payload_json", "type": "string", "index": False},
    ],
}


class TypesenseClient:
    def __init__(self, base_url: str, api_key: str, collection: str = "products") -> None:
        self._base_url = base_url.rstrip("/")
        self._collection = collection
        self._headers = {"X-TYPESENSE-API-KEY": api_key}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers,
                params=params,
                json=json,
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise TypesenseError(f"Typesense request failed: {method} {path}") from exc
        if allow_not_found and response.status_code == 404:
            return response
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TypesenseError(
                f"Typesense rejected {method} {path}: {response.text[:500]}"
            ) from exc
        return response

    def ensure_collection(self) -> None:
        response = self._request("GET", f"/collections/{self._collection}", allow_not_found=True)
        if response.status_code == 404:
            schema = {**COLLECTION_SCHEMA, "name": self._collection}
            self._request("POST", "/collections", json=schema)

    def reset_collection(self) -> None:
        self._request("DELETE", f"/collections/{self._collection}", allow_not_found=True)
        schema = {**COLLECTION_SCHEMA, "name": self._collection}
        self._request("POST", "/collections", json=schema)

    def upsert(self, document: dict[str, Any]) -> None:
        product_uid = str(document["product_uid"])
        body = {**document, "id": product_uid}
        self._request(
            "POST",
            f"/collections/{self._collection}/documents",
            params={"action": "upsert"},
            json=body,
        )

    def delete(self, product_uid: str) -> None:
        self._request(
            "DELETE",
            f"/collections/{self._collection}/documents/{product_uid}",
            allow_not_found=True,
        )

    def _search(self, params: dict[str, str | int]) -> list[dict[str, Any]]:
        response = self._request(
            "GET", f"/collections/{self._collection}/documents/search", params=params
        )
        body = response.json()
        hits = body.get("hits", [])
        return [hit["document"] for hit in hits if isinstance(hit.get("document"), dict)]

    def _multi_search(self, params: dict[str, str | int]) -> list[dict[str, Any]]:
        search = {"collection": self._collection, **params}
        response = self._request("POST", "/multi_search", json={"searches": [search]})
        results = response.json().get("results", [])
        if not results:
            return []
        hits = results[0].get("hits", [])
        return [hit["document"] for hit in hits if isinstance(hit.get("document"), dict)]

    @staticmethod
    def _with_filter(
        params: dict[str, str | int], filters: SearchFilters, vehicle_slug: str | None
    ) -> dict[str, str | int]:
        filter_by = build_filter_by(filters, vehicle_slug)
        if filter_by:
            params["filter_by"] = filter_by
        return params

    def exact_search(
        self,
        part_number: str,
        filters: SearchFilters,
        vehicle_slug: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "q": "*",
            "filter_by": f"part_numbers:=[`{part_number}`]",
            "per_page": limit,
            "sort_by": "offer_count:desc,best_seller_trust:desc",
        }
        additional = build_filter_by(filters, vehicle_slug)
        if additional:
            params["filter_by"] = f"{params['filter_by']} && {additional}"
        return self._search(params)

    def lexical_search(
        self,
        query: str,
        filters: SearchFilters,
        vehicle_slug: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "q": query,
            "query_by": "title,title_variants,brand,part_type_synonyms",
            "query_by_weights": "8,5,3,4",
            "num_typos": "1,1,1,1",
            "prefix": "false,false,false,false",
            "per_page": limit,
        }
        return self._multi_search(self._with_filter(params, filters, vehicle_slug))

    def vector_search(
        self,
        vector: list[float],
        filters: SearchFilters,
        vehicle_slug: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        vector_text = ",".join(f"{value:.8f}" for value in vector)
        params: dict[str, str | int] = {
            "q": "*",
            "vector_query": f"embedding:([{vector_text}], k:{limit})",
            "per_page": limit,
        }
        return self._multi_search(self._with_filter(params, filters, vehicle_slug))

    def suggest(self, query: str, limit: int) -> list[dict[str, Any]]:
        return self._search(
            {
                "q": query,
                "query_by": "title,title_variants,part_numbers",
                "query_by_weights": "8,5,6",
                "prefix": "true,true,true",
                "per_page": limit,
            }
        )

    def upsert_synonym(self, part_type: str, tokens: list[str]) -> None:
        synonyms = sorted({part_type, *tokens})
        synonym_id = quote(f"part-type-{part_type}", safe="")
        self._request(
            "PUT",
            f"/collections/{self._collection}/synonyms/{synonym_id}",
            json={"synonyms": synonyms},
        )

    def delete_synonym(self, part_type: str) -> None:
        synonym_id = quote(f"part-type-{part_type}", safe="")
        self._request(
            "DELETE",
            f"/collections/{self._collection}/synonyms/{synonym_id}",
            allow_not_found=True,
        )

    def health(self) -> bool:
        try:
            response = self._request("GET", "/health")
        except TypesenseError:
            return False
        return response.json().get("ok") is True
