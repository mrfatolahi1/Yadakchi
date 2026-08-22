from __future__ import annotations

import logging

from django.db import connection
from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from redis import Redis

from search.query_builder import SearchFilters
from search.query_log import record_click, record_query
from search.schema import (
    AcceptedResponse,
    ClickEventIn,
    ErrorResponse,
    HealthResponse,
    SearchResponse,
    SuggestResponse,
)
from search.services import get_index, get_query_service
from search.settings import SEARCH_REDIS_URL

logger = logging.getLogger(__name__)
SEARCH_REQUESTS = Counter("search_requests_total", "Search API requests", ("status",))
SEARCH_DURATION = Histogram("search_request_seconds", "Search request duration")
ZERO_RESULTS = Counter("search_zero_results_total", "Queries returning zero results")

api = NinjaAPI(title="yadakchi search", version="1.0.0", urls_namespace="search-api")


@api.get(
    "/v1/search",
    response={200: SearchResponse, 503: ErrorResponse},
    tags=["search"],
)
def search(
    request: HttpRequest,
    q: str,
    vehicle_slug: str | None = None,
    brand: str | None = None,
    part_type: str | None = None,
    authenticity: str | None = None,
    min_price_toman: int | None = None,
    max_price_toman: int | None = None,
    has_image: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[int, object]:
    del request
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    filters = SearchFilters(
        brand=brand,
        part_type=part_type,
        authenticity=authenticity,
        min_price_toman=min_price_toman,
        max_price_toman=max_price_toman,
        has_image=has_image,
    )
    try:
        with SEARCH_DURATION.time():
            result = get_query_service().search(q, filters, vehicle_slug, page, page_size)
        query_log = record_query(
            result.normalized_query,
            vehicle_slug,
            filters,
            result.total,
            [str(hit["product_uid"]) for hit in result.hits],
        )
    except Exception:
        SEARCH_REQUESTS.labels(status="error").inc()
        logger.exception("search request failed")
        return 503, {
            "code": "search_unavailable",
            "message": "search is temporarily unavailable",
            "detail": None,
        }
    SEARCH_REQUESTS.labels(status="ok").inc()
    if result.total == 0:
        ZERO_RESULTS.inc()
    return 200, {
        "query_id": query_log.query_id,
        "normalized_query": result.normalized_query,
        "page": result.page,
        "page_size": result.page_size,
        "total": result.total,
        "fallback_applied": result.fallback_applied,
        "hits": result.hits,
        "facets": result.facets,
    }


@api.get(
    "/v1/suggest",
    response={200: SuggestResponse, 503: ErrorResponse},
    tags=["search"],
)
def suggest(request: HttpRequest, q: str, limit: int = 10) -> tuple[int, object]:
    del request
    try:
        normalized, suggestions = get_query_service().suggest(q, min(20, max(1, limit)))
    except Exception:
        logger.exception("suggest request failed")
        return 503, {
            "code": "suggest_unavailable",
            "message": "suggestions are temporarily unavailable",
            "detail": None,
        }
    return 200, {"normalized_query": normalized, "suggestions": suggestions}


@api.post("/v1/events/click", response={202: AcceptedResponse}, tags=["events"])
def click(request: HttpRequest, payload: ClickEventIn) -> tuple[int, dict[str, bool]]:
    del request
    try:
        record_click(payload.query_id, payload.product_uid, payload.position)
    except Exception:
        logger.exception(
            "result click could not be recorded", extra={"query_id": str(payload.query_id)}
        )
    return 202, {"accepted": True}


@api.get("/v1/health", response=HealthResponse, tags=["operations"])
def health(request: HttpRequest) -> dict[str, object]:
    del request
    components: dict[str, str] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        components["postgres"] = "up"
    except Exception:
        components["postgres"] = "down"
    components["typesense"] = "up" if get_index().health() else "down"
    try:
        redis = Redis.from_url(SEARCH_REDIS_URL, socket_connect_timeout=0.5)
        components["redis"] = "up" if redis.ping() else "down"
    except Exception:
        components["redis"] = "down"
    return {
        "status": "ok" if all(value == "up" for value in components.values()) else "degraded",
        "components": components,
    }


@api.get("/metrics", response=None, tags=["operations"])
def metrics_view(request: HttpRequest) -> HttpResponse:
    del request
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
