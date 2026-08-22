from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, cast

from search.ai_client import EmbeddingClient
from search.query_builder import SearchFilters
from search.ranking import RankedDocument, rank_documents
from search.text import looks_like_part_number, normalize_part_number, normalize_text
from search.typesense_client import SearchIndex


@dataclass(frozen=True, slots=True)
class SearchPage:
    normalized_query: str
    page: int
    page_size: int
    total: int
    fallback_applied: bool
    hits: list[dict[str, Any]]
    facets: dict[str, list[dict[str, str | int]]]


def _facet_buckets(counter: Counter[str]) -> list[dict[str, str | int]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _facets(ranked: list[RankedDocument]) -> dict[str, list[dict[str, str | int]]]:
    vehicles: Counter[str] = Counter()
    brands: Counter[str] = Counter()
    part_types: Counter[str] = Counter()
    authenticity: Counter[str] = Counter()
    price_ranges: Counter[str] = Counter()
    for item in ranked:
        document = item.document
        compatible = document.get("vehicle_compatible", [])
        if isinstance(compatible, list):
            vehicles.update(str(value) for value in compatible)
        if document.get("brand"):
            brands[str(document["brand"])] += 1
        if document.get("part_type"):
            part_types[str(document["part_type"])] += 1
        if document.get("authenticity_dominant"):
            authenticity[str(document["authenticity_dominant"])] += 1
        price = document.get("min_price_toman")
        if isinstance(price, int):
            if price < 1_000_000:
                price_ranges["under_1m"] += 1
            elif price < 5_000_000:
                price_ranges["1m_to_5m"] += 1
            else:
                price_ranges["5m_and_over"] += 1
    return {
        "vehicles": _facet_buckets(vehicles),
        "brands": _facet_buckets(brands),
        "part_types": _facet_buckets(part_types),
        "authenticity": _facet_buckets(authenticity),
        "price_ranges": _facet_buckets(price_ranges),
    }


def _fitment_status(document: dict[str, Any], vehicle_slug: str | None) -> str:
    if not vehicle_slug:
        return "not_requested"
    compatible = document.get("vehicle_compatible", [])
    incompatible = document.get("vehicle_incompatible", [])
    if isinstance(compatible, list) and vehicle_slug in compatible:
        return "fits"
    if isinstance(incompatible, list) and vehicle_slug in incompatible:
        return "incompatible"
    return "unverified"


def _render_hit(item: RankedDocument, vehicle_slug: str | None) -> dict[str, Any]:
    payload = cast(dict[str, Any], json.loads(str(item.document["payload_json"])))
    payload["fitment_status"] = _fitment_status(item.document, vehicle_slug)
    payload["exact_part_number_match"] = item.exact_part_number_match
    return payload


class QueryService:
    def __init__(
        self,
        index: SearchIndex,
        embedding_client: EmbeddingClient,
        *,
        result_floor: int = 5,
        candidate_limit: int = 250,
    ) -> None:
        self._index = index
        self._embedding_client = embedding_client
        self._result_floor = result_floor
        self._candidate_limit = candidate_limit

    def _retrieve(
        self,
        normalized_query: str,
        query_vector: list[float],
        filters: SearchFilters,
        vehicle_filter: str | None,
        vehicle_for_ranking: str | None,
    ) -> list[RankedDocument]:
        exact: list[dict[str, Any]] = []
        if looks_like_part_number(normalized_query):
            exact = self._index.exact_search(
                normalize_part_number(normalized_query),
                filters,
                vehicle_filter,
                self._candidate_limit,
            )
        lexical = self._index.lexical_search(
            normalized_query, filters, vehicle_filter, self._candidate_limit
        )
        vector = self._index.vector_search(
            query_vector, filters, vehicle_filter, self._candidate_limit
        )
        return rank_documents(exact, lexical, vector, vehicle_for_ranking)

    def search(
        self,
        query: str,
        filters: SearchFilters,
        vehicle_slug: str | None,
        page: int,
        page_size: int,
    ) -> SearchPage:
        normalized_query = normalize_text(query)
        if not normalized_query:
            ranked: list[RankedDocument] = []
        else:
            query_vector = self._embedding_client.embed([normalized_query])[0]
            ranked = self._retrieve(
                normalized_query,
                query_vector,
                filters,
                vehicle_slug,
                vehicle_slug,
            )
        fallback_applied = bool(vehicle_slug and len(ranked) < self._result_floor)
        if fallback_applied and normalized_query:
            ranked = self._retrieve(
                normalized_query,
                query_vector,
                filters,
                None,
                vehicle_slug,
            )
        total = len(ranked)
        start = (page - 1) * page_size
        page_items = ranked[start : start + page_size]
        return SearchPage(
            normalized_query=normalized_query,
            page=page,
            page_size=page_size,
            total=total,
            fallback_applied=fallback_applied,
            hits=[_render_hit(item, vehicle_slug) for item in page_items],
            facets=_facets(ranked),
        )

    def suggest(self, query: str, limit: int) -> tuple[str, list[dict[str, Any]]]:
        normalized = normalize_text(query)
        if not normalized:
            return normalized, []
        documents = self._index.suggest(normalized, limit)
        suggestions: list[dict[str, Any]] = []
        for document in documents:
            part_numbers = document.get("part_numbers", [])
            suggestions.append(
                {
                    "product_uid": document["product_uid"],
                    "text": document["title"],
                    "part_number": part_numbers[0]
                    if isinstance(part_numbers, list) and part_numbers
                    else None,
                }
            )
        return normalized, suggestions
