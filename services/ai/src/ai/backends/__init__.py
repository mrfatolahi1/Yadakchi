"""Model backends: one HTTP client for every real provider, plus the stub."""

from __future__ import annotations

import httpx

from ai.backends.base import Completion, CompletionRequest, ModelBackend
from ai.backends.http import HttpBackend, build_client
from ai.backends.stub import StubBackend
from ai.config import Backend, Settings

__all__ = [
    "Completion",
    "CompletionRequest",
    "HttpBackend",
    "ModelBackend",
    "StubBackend",
    "build_backend",
    "build_client",
]


def build_backend(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelBackend:
    """Pick the backend `AI_BACKEND` names. Four values, two implementations."""
    if settings.ai_backend is Backend.STUB:
        return StubBackend()
    return HttpBackend(settings, transport=transport)
