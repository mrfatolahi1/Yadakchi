"""One HTTP client for every real provider.

`local`, `domestic` and `external` are not three integrations. They are one
OpenAI-compatible client and three sets of `AI_BASE_URL` / `AI_API_KEY` /
`AI_MODEL`. Switching between them changes the base URL and nothing else —
there is a test that proves exactly that, because the moment this file grows a
`if backend == "domestic"` branch, the three stop being interchangeable.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ai.backends.base import Completion, CompletionRequest, ModelBackend
from ai.config import Backend, Settings
from ai.errors import BackendError, BackendUnavailableError
from ai.logging_ import get_logger
from ai.metrics import UPSTREAM_ATTEMPTS

logger = get_logger(__name__)

#: Statuses worth trying again: the provider is overloaded, restarting, or
#: rate-limiting us. Anything else is our fault and retrying just wastes budget.
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def build_client(
    *,
    base_url: str,
    api_key: str | None,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """The one client. `transport` is how tests plug in a mocked server."""
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=httpx.Timeout(timeout),
        transport=transport,
    )


class HttpBackend(ModelBackend):
    """An OpenAI-compatible `/chat/completions` endpoint."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = settings.base_url
        if not base_url:  # pragma: no cover - Settings validation rejects this first
            raise BackendError("AI_BASE_URL is not configured")
        self.name = settings.ai_backend.value
        self._settings = settings
        self._model = settings.ai_model
        self._client = build_client(
            base_url=base_url,
            api_key=settings.ai_api_key,
            timeout=settings.ai_timeout_seconds,
            transport=transport,
        )

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    def _body(self, request: CompletionRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        if self._settings.ai_json_mode:
            # Ollama and vLLM both honour this; an endpoint that does not can
            # turn it off with AI_JSON_MODE=false without a code change.
            body["response_format"] = {"type": "json_object"}
        return body

    async def complete(self, request: CompletionRequest) -> Completion:
        attempts = self._settings.ai_max_attempts
        backoff = self._settings.ai_retry_backoff_seconds
        started = time.perf_counter()
        last_error: str = "no attempt was made"

        # Every path that reaches the bottom of this loop is a retryable one:
        # the others either return an answer or raise immediately.
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.post("chat/completions", json=self._body(request))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code in RETRYABLE_STATUSES:
                    last_error = f"HTTP {response.status_code}"
                elif response.status_code >= 400:
                    UPSTREAM_ATTEMPTS.labels(op=request.op, outcome="failed").inc()
                    raise BackendError(
                        f"model provider rejected the request with HTTP {response.status_code}",
                        {"status": response.status_code, "backend": self.name},
                    )
                else:
                    UPSTREAM_ATTEMPTS.labels(op=request.op, outcome="ok").inc()
                    return self._parse(response, started)

            if attempt < attempts:
                UPSTREAM_ATTEMPTS.labels(op=request.op, outcome="retry").inc()
                logger.warning(
                    "model provider call failed, retrying",
                    extra={
                        "op": request.op,
                        "backend": self.name,
                        "attempt": attempt,
                        "attempts": attempts,
                        "error": last_error,
                    },
                )
                await asyncio.sleep(backoff * (2 ** (attempt - 1)))

        UPSTREAM_ATTEMPTS.labels(op=request.op, outcome="failed").inc()
        raise BackendUnavailableError(
            f"model provider unreachable after {attempts} attempt(s): {last_error}",
            {"backend": self.name, "attempts": attempts},
        )

    def _parse(self, response: httpx.Response, started: float) -> Completion:
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise BackendError(
                "model provider returned a body that is not an OpenAI chat completion",
                {"backend": self.name, "error": str(exc)},
            ) from exc
        if not isinstance(content, str):
            raise BackendError(
                "model provider returned a non-text completion",
                {"backend": self.name},
            )
        usage = data.get("usage") or {}
        return Completion(
            text=content,
            model=str(data.get("model") or self._model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            duration_seconds=time.perf_counter() - started,
        )

    async def reachable(self) -> bool:
        """A cheap GET /models. Reported by /health, never on the hot path."""
        try:
            response = await self._client.get("models", timeout=httpx.Timeout(5.0))
        except (httpx.TimeoutException, httpx.TransportError):
            return False
        return response.status_code < 500

    async def aclose(self) -> None:
        await self._client.aclose()


def http_backend_factory(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HttpBackend:
    if settings.ai_backend is Backend.STUB:  # pragma: no cover - guarded by the caller
        raise BackendError("the stub backend does not speak HTTP")
    return HttpBackend(settings, transport=transport)
