from __future__ import annotations

from functools import lru_cache

from django.conf import settings

from search.ai_client import AiClient, EmbeddingClient
from search.search_service import QueryService
from search.typesense_client import SearchIndex, TypesenseClient


@lru_cache(maxsize=1)
def get_index() -> SearchIndex:
    return TypesenseClient(
        settings.TYPESENSE_URL,
        settings.TYPESENSE_API_KEY,
        settings.TYPESENSE_COLLECTION,
    )


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    return AiClient(settings.AI_BASE_URL, settings.AI_TIMEOUT_SECONDS)


@lru_cache(maxsize=1)
def get_query_service() -> QueryService:
    return QueryService(
        get_index(),
        get_embedding_client(),
        result_floor=settings.SEARCH_RESULT_FLOOR,
        candidate_limit=settings.SEARCH_CANDIDATE_LIMIT,
    )
