from __future__ import annotations

import json
import math
from typing import Any

from search.query_builder import SearchFilters, document_matches
from search.text import normalize_text


class FakeEmbeddings:
    def __init__(self, mapping: dict[str, list[float]] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [self.mapping.get(text, self._default(text)) for text in texts]

    @staticmethod
    def _default(text: str) -> list[float]:
        vector = [0.0] * 384
        for index, byte in enumerate(text.encode("utf-8")):
            vector[index % 384] += (byte % 31) / 31
        magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / magnitude for value in vector]


class FakeIndex:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.synonyms: dict[str, list[str]] = {}
        self.upsert_count = 0
        self.delete_count = 0
        self.forced_lexical: list[str] | None = None
        self.forced_vector: list[str] | None = None

    def ensure_collection(self) -> None:
        return None

    def reset_collection(self) -> None:
        self.documents.clear()
        self.synonyms.clear()

    def upsert(self, document: dict[str, Any]) -> None:
        self.upsert_count += 1
        self.documents[str(document["product_uid"])] = json.loads(json.dumps(document))

    def delete(self, product_uid: str) -> None:
        self.delete_count += 1
        self.documents.pop(product_uid, None)

    def _filtered(self, filters: SearchFilters, vehicle_slug: str | None) -> list[dict[str, Any]]:
        return [
            document
            for document in self.documents.values()
            if document_matches(document, filters, vehicle_slug)
        ]

    def exact_search(
        self,
        part_number: str,
        filters: SearchFilters,
        vehicle_slug: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return [
            document
            for document in self._filtered(filters, vehicle_slug)
            if part_number in document.get("part_numbers", [])
        ][:limit]

    def lexical_search(
        self,
        query: str,
        filters: SearchFilters,
        vehicle_slug: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        candidates = self._filtered(filters, vehicle_slug)
        if self.forced_lexical is not None:
            by_uid = {str(document["product_uid"]): document for document in candidates}
            return [by_uid[uid] for uid in self.forced_lexical if uid in by_uid][:limit]
        query_tokens = set(normalize_text(query).casefold().split())
        scored: list[tuple[int, dict[str, Any]]] = []
        for document in candidates:
            text = " ".join(
                [
                    str(document.get("title", "")),
                    *[str(value) for value in document.get("title_variants", [])],
                    str(document.get("brand", "")),
                    *[str(value) for value in document.get("part_type_synonyms", [])],
                ]
            ).casefold()
            score = sum(token in text for token in query_tokens)
            if score:
                scored.append((score, document))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [document for _, document in scored[:limit]]

    def vector_search(
        self,
        vector: list[float],
        filters: SearchFilters,
        vehicle_slug: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        candidates = self._filtered(filters, vehicle_slug)
        if self.forced_vector is not None:
            by_uid = {str(document["product_uid"]): document for document in candidates}
            return [by_uid[uid] for uid in self.forced_vector if uid in by_uid][:limit]

        def similarity(document: dict[str, Any]) -> float:
            embedding = document.get("embedding", [])
            if not isinstance(embedding, list):
                return -1.0
            return sum(a * float(b) for a, b in zip(vector, embedding, strict=False))

        return sorted(candidates, key=similarity, reverse=True)[:limit]

    def suggest(self, query: str, limit: int) -> list[dict[str, Any]]:
        normalized = normalize_text(query).casefold()
        return [
            document
            for document in self.documents.values()
            if normalized in str(document.get("title", "")).casefold()
        ][:limit]

    def upsert_synonym(self, part_type: str, tokens: list[str]) -> None:
        self.synonyms[part_type] = sorted(tokens)

    def delete_synonym(self, part_type: str) -> None:
        self.synonyms.pop(part_type, None)

    def health(self) -> bool:
        return True
