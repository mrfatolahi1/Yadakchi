"""The FastAPI application.

This is the only service in `yadakchi` that is not Django and the only one
that may import a model SDK, so everything the rest of the system needs from a
language model has to be exposed here — three endpoints, no more, and no way
for a caller to pass a prompt of its own.

The OpenAPI document this produces is the contract: it is committed to
`contracts/published/openapi.json` and `enricher`, `matcher` and `search`
generate their clients from it. A test fails the build when the two disagree.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST
from redis.asyncio import Redis

from ai import API_VERSION
from ai.api_models import (
    BudgetStatus,
    CacheStatus,
    EmbedRequest,
    EmbedResponse,
    ErrorResponse,
    ExtractRequest,
    ExtractResponse,
    HealthResponse,
    JudgeRequest,
    JudgeResponse,
)
from ai.backends import ModelBackend, build_backend
from ai.budget import BudgetGuard
from ai.cache import Cache
from ai.config import EMBEDDING_DIM, Settings, get_settings
from ai.embeddings import EmbeddingProvider, build_embedder
from ai.errors import AIServiceError, BudgetExhaustedError, InvalidRequestError
from ai.logging_ import configure_logging, get_logger
from ai.metrics import render as render_metrics
from ai.prompts import PROMPT_VERSION
from ai.schemas import schema_names
from ai.service import AIService

logger = get_logger(__name__)

DESCRIPTION = """
Field extraction, pair adjudication and embeddings for yadakchi.

This is the only service allowed to talk to a language model; no other service
may import a model SDK. It is stateless and has no database.

* `POST /v1/extract` — structured fields from a Persian listing title.
* `POST /v1/judge` — are these two listings the same product?
* `POST /v1/embed` — 384-dimension vectors, always exactly 384.

Errors share one body: `{"code", "message", "detail"}`. The code that matters
most to callers is **`budget_exhausted`** (HTTP 429): today's model budget is
spent, and the caller should fall back to its own rules until UTC midnight.
""".strip()

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Unknown schema name."},
    422: {"model": ErrorResponse, "description": "Invalid request, or an unusable model answer."},
    429: {
        "model": ErrorResponse,
        "description": "Daily model budget exhausted (`budget_exhausted`). Fall back to rules.",
    },
    502: {"model": ErrorResponse, "description": "The model provider answered unusably."},
    503: {"model": ErrorResponse, "description": "The model provider is unreachable."},
}


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    redis: Redis | None = None,
    backend: ModelBackend | None = None,
    embedder: EmbeddingProvider | None = None,
) -> FastAPI:
    """Build the app. Every collaborator can be replaced, which is how the
    tests reach a mocked provider without a socket."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level, resolved.log_format)
    _init_sentry(resolved)

    app = FastAPI(
        title="yadakchi ai",
        version=API_VERSION,
        description=DESCRIPTION,
        summary="Field extraction, pair adjudication and embeddings.",
        lifespan=_lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        contact={"name": "yadakchi", "url": "https://github.com/yadakchi"},
        license_info={"name": "Proprietary"},
        servers=[{"url": "http://ai:8000", "description": "inside the yadakchi network"}],
    )

    app.state.settings = resolved
    app.state.transport = transport
    app.state.redis = redis
    app.state.backend_override = backend
    app.state.embedder_override = embedder
    app.state.probe = (0.0, False)

    _register_handlers(app)
    _register_routes(app)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot. Anything wrong with the configuration stops the process here."""
    settings: Settings = app.state.settings
    # Again, not only in create_app(): uvicorn installs its own handlers when
    # the server starts, which is after this module was imported. Re-applying
    # here is what keeps every line on stdout a single JSON object.
    configure_logging(settings.log_level, settings.log_format)

    backend: ModelBackend = app.state.backend_override or build_backend(
        settings, transport=app.state.transport
    )
    embedder: EmbeddingProvider = app.state.embedder_override or build_embedder(
        settings, transport=app.state.transport
    )
    cache = Cache(settings, redis=app.state.redis)
    await cache.connect()
    budget = BudgetGuard(settings, cache)

    # The 384-dimension contract, verified before the first request rather
    # than discovered by `matcher` on a write.
    dimension = await embedder.startup_check()
    if dimension != EMBEDDING_DIM:  # pragma: no cover - providers raise first
        raise RuntimeError(f"embedding width is {dimension}, expected {EMBEDDING_DIM}")

    app.state.service = AIService(
        settings,
        backend=backend,
        embedder=embedder,
        cache=cache,
        budget=budget,
    )
    logger.info(
        "ai service ready",
        extra={
            "backend": backend.name,
            "model": backend.model_id,
            "embed_backend": embedder.name,
            "embed_model": embedder.model_id,
            "dim": dimension,
            "prompt_version": PROMPT_VERSION,
            "cache": cache.redis_status,
            "budget_limit": settings.ai_daily_budget,
            "budget_unit": settings.budget_unit,
        },
    )
    try:
        yield
    finally:
        await backend.aclose()
        await embedder.aclose()
        await cache.aclose()


def _service(request: Request) -> AIService:
    service: AIService = request.app.state.service
    return service


def _register_routes(app: FastAPI) -> None:
    @app.post(
        "/v1/extract",
        response_model=ExtractResponse,
        operation_id="extract",
        summary="Extract structured fields from a Persian listing title",
        tags=["inference"],
        responses=ERROR_RESPONSES,
    )
    async def extract(request: Request, body: ExtractRequest) -> ExtractResponse:
        return await _service(request).extract(body)

    @app.post(
        "/v1/judge",
        response_model=JudgeResponse,
        operation_id="judge",
        summary="Decide whether two listings are the same product",
        tags=["inference"],
        responses=ERROR_RESPONSES,
    )
    async def judge(request: Request, body: JudgeRequest) -> JudgeResponse:
        return await _service(request).judge(body)

    @app.post(
        "/v1/embed",
        response_model=EmbedResponse,
        operation_id="embed",
        summary="Embed up to 256 texts as 384-dimension vectors",
        tags=["inference"],
        responses=ERROR_RESPONSES,
    )
    async def embed(request: Request, body: EmbedRequest) -> EmbedResponse:
        return await _service(request).embed(body)

    @app.get(
        "/health",
        response_model=HealthResponse,
        operation_id="health",
        summary="Backend, model, reachability and today's budget",
        tags=["operations"],
    )
    async def health(request: Request) -> HealthResponse:
        service = _service(request)
        settings: Settings = request.app.state.settings
        reachable = await _probe(request.app, service)
        budget = await service.budget.snapshot()
        healthy = reachable and not budget.exhausted and service.cache.redis_status != "down"
        return HealthResponse(
            status="ok" if healthy else "degraded",
            version=API_VERSION,
            backend=service.backend.name,
            model=service.backend.model_id,
            reachable=reachable,
            embed_backend=service.embedder.name,
            embed_model=service.embedder.model_id,
            dim=EMBEDDING_DIM,
            prompt_version=PROMPT_VERSION,
            schemas=list(schema_names()),
            cache=CacheStatus(
                redis=service.cache.redis_status,
                entries=service.cache.entries,
                ttl_seconds=settings.ai_cache_ttl_seconds,
            ),
            budget=BudgetStatus(
                day=budget.day,
                used=budget.used,
                limit=budget.limit,
                ratio=budget.ratio,
                unit=budget.unit,  # type: ignore[arg-type]
                enabled=budget.enabled,
                exhausted=budget.exhausted,
            ),
        )

    @app.get(
        "/metrics",
        operation_id="metrics",
        summary="Prometheus metrics",
        tags=["operations"],
        response_class=PlainTextResponse,
        responses={200: {"content": {CONTENT_TYPE_LATEST: {}}}},
    )
    async def metrics() -> Response:
        return Response(content=render_metrics(), media_type=CONTENT_TYPE_LATEST)


async def _probe(app: FastAPI, service: AIService) -> bool:
    """Reachability, remembered briefly so /health cannot become a load test."""
    settings: Settings = app.state.settings
    checked_at, last = app.state.probe
    now = time.monotonic()
    if now - checked_at < settings.ai_health_probe_ttl_seconds and checked_at > 0.0:
        return bool(last)
    reachable = await service.backend.reachable()
    app.state.probe = (now, reachable)
    return reachable


def _register_handlers(app: FastAPI) -> None:
    @app.exception_handler(AIServiceError)
    async def _service_error(request: Request, exc: Exception) -> JSONResponse:
        error = exc if isinstance(exc, AIServiceError) else AIServiceError(str(exc))
        headers: dict[str, str] = {}
        if isinstance(error, BudgetExhaustedError):
            service: AIService = request.app.state.service
            headers["Retry-After"] = str(service.budget.seconds_until_reset())
            logger.warning("refusing a call: daily budget exhausted", extra=error.log_fields())
        elif error.status_code >= 500:
            logger.error("request failed", extra=error.log_fields())
        return JSONResponse(status_code=error.status_code, content=error.body(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
        # FastAPI's default 422 body is a list under "detail"; give callers the
        # same {code, message, detail} envelope as every other error instead.
        errors = exc.errors() if isinstance(exc, RequestValidationError) else []
        error = InvalidRequestError(
            "the request body is not valid",
            {"errors": [{k: v for k, v in item.items() if k != "ctx"} for item in errors]},
        )
        return JSONResponse(status_code=error.status_code, content=error.body())


def _init_sentry(settings: Settings) -> None:
    """Error reporting, and never a reason for the service to be down.

    Two traps, both of which have already bitten:

    * `docker compose` does not strip inline comments from a `.env` value, so
      `SENTRY_DSN=            # empty disables Sentry` arrives as the literal
      string "# empty disables Sentry". That is not a DSN;
    * an unusable DSN makes `sentry_sdk.init` raise, which — from module scope
      — turns a monitoring misconfiguration into a crash loop.

    So a DSN that is not a URL is treated as absent, and any failure to start
    Sentry is logged and stepped over.
    """
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        return
    if "://" not in dsn:
        logger.warning(
            "SENTRY_DSN is not a URL — ignoring it and running without Sentry. "
            "A trailing comment in an .env file is the usual cause: compose keeps it "
            "as part of the value.",
            extra={"length": len(dsn)},
        )
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=settings.sentry_environment,
            traces_sample_rate=0.05,
            # Titles are business data and prompts are built from them.
            send_default_pii=False,
        )
    except ImportError:  # pragma: no cover - sentry-sdk is in requirements.txt
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
    except Exception as exc:
        logger.warning(
            "could not start Sentry — continuing without it",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )


#: The ASGI entry point: `uvicorn ai.main:app`.
app = create_app()
