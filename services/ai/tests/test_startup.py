"""Startup checks — a wrong setting stops the process, it never leaks into a call.

`matcher` writes these vectors into a pgvector column declared as 384 and
`search` into a Typesense field declared as 384. A service that discovered a
width mismatch on its first call would already have taken the reprocess with
it, so the check happens before the first request.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.config import EMBEDDING_DIM, Backend, EmbedBackend, Settings, get_settings
from ai.embeddings import LocalEmbedder
from ai.errors import ConfigurationError
from ai.main import create_app


def test_a_known_wrong_embedding_model_fails_before_a_socket_is_opened() -> None:
    """Acceptance criterion 3, the offline half: no network needed to say no."""
    with pytest.raises(ConfigurationError) as raised:
        Settings(
            ai_backend=Backend.STUB,
            ai_embed_backend=EmbedBackend.LOCAL,
            ai_embed_model="intfloat/multilingual-e5-base",
        )

    assert "768" in str(raised.value)
    assert str(EMBEDDING_DIM) in str(raised.value)


@pytest.mark.parametrize(
    "model",
    ["BAAI/bge-m3", "text-embedding-3-small", "intfloat/multilingual-e5-large"],
)
def test_every_known_wide_model_is_refused(model: str) -> None:
    with pytest.raises(ConfigurationError):
        Settings(ai_backend=Backend.STUB, ai_embed_backend=EmbedBackend.LOCAL, ai_embed_model=model)


def test_a_known_384_model_is_accepted() -> None:
    settings = Settings(
        ai_backend=Backend.STUB,
        ai_embed_backend=EmbedBackend.LOCAL,
        ai_embed_model="intfloat/multilingual-e5-small",
    )
    assert settings.embed_backend is EmbedBackend.LOCAL


def test_the_process_refuses_to_boot_with_a_wrong_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real boot path: uvicorn imports ai.main, which calls create_app()."""
    monkeypatch.setenv("AI_EMBED_BACKEND", "local")
    monkeypatch.setenv("AI_EMBED_MODEL", "BAAI/bge-m3")
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError):
        create_app()


class FakeSentenceTransformer:
    def __init__(self, dimension: int) -> None:
        self._dimension = dimension

    def get_sentence_embedding_dimension(self) -> int:
        return self._dimension


async def test_a_model_of_unknown_width_is_measured_when_it_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlisted model is not taken on trust — it is weighed at startup."""
    settings = Settings(
        ai_backend=Backend.STUB, ai_embed_backend=EmbedBackend.LOCAL, ai_embed_model="some/model"
    )
    embedder = LocalEmbedder(settings)
    monkeypatch.setattr(LocalEmbedder, "_load", lambda self: FakeSentenceTransformer(768))

    with pytest.raises(ConfigurationError) as raised:
        await embedder.startup_check()

    assert "768" in str(raised.value)


async def test_a_correct_unlisted_model_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        ai_backend=Backend.STUB, ai_embed_backend=EmbedBackend.LOCAL, ai_embed_model="some/model"
    )
    embedder = LocalEmbedder(settings)
    monkeypatch.setattr(LocalEmbedder, "_load", lambda self: FakeSentenceTransformer(384))

    assert await embedder.startup_check() == EMBEDDING_DIM


def test_an_http_embedder_of_the_wrong_width_fails_at_startup(
    make_app: Callable[..., Any],
) -> None:
    def wide(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 768}]})

    app = make_app(
        transport=httpx.MockTransport(wide),
        ai_backend="local",
        ai_base_url="http://p/v1",
        ai_embed_backend="http",
        ai_embed_model="some/remote-model",
    )

    with pytest.raises(ConfigurationError), TestClient(app):
        pass  # pragma: no cover - the lifespan raises first


def test_an_http_embedder_of_the_right_width_starts(make_app: Callable[..., Any]) -> None:
    def right(request: httpx.Request) -> httpx.Response:
        payload = [
            {"index": index, "embedding": [0.1] * EMBEDDING_DIM}
            for index, _ in enumerate(request.content.decode().split('","'))
        ]
        return httpx.Response(200, json={"data": payload[:1]})

    app = make_app(
        transport=httpx.MockTransport(right),
        ai_backend="local",
        ai_base_url="http://p/v1",
        ai_embed_backend="http",
        ai_embed_model="some/remote-model",
    )

    with TestClient(app) as client:
        assert client.get("/health").json()["embed_backend"] == "http"


def test_a_remote_backend_without_a_base_url_is_refused() -> None:
    with pytest.raises(ConfigurationError) as raised:
        Settings(ai_backend=Backend.DOMESTIC, ai_base_url=None)

    assert "AI_BASE_URL" in str(raised.value)


def test_a_budget_of_zero_is_refused_because_it_would_refuse_everything() -> None:
    with pytest.raises(ConfigurationError):
        Settings(ai_backend=Backend.STUB, ai_daily_budget=0.0)

    assert Settings(ai_backend=Backend.STUB, ai_budget_enabled=False, ai_daily_budget=0.0)


def test_the_stub_is_the_default_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clone with no environment at all must work offline."""
    for name in ("AI_BACKEND", "AI_BASE_URL", "AI_EMBED_BACKEND", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ai_backend is Backend.STUB
    assert settings.embed_backend is EmbedBackend.STUB
    assert settings.base_url is None


def test_empty_environment_variables_are_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docker compose` writes "" for a variable nobody set."""
    for name, value in (
        ("AI_BACKEND", "stub"),
        ("AI_BASE_URL", ""),
        ("AI_API_KEY", ""),
        ("AI_EMBED_BACKEND", ""),
        ("REDIS_URL", ""),
        ("SENTRY_DSN", ""),
    ):
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ai_base_url is None
    assert settings.ai_embed_backend is None
    assert settings.embed_backend is EmbedBackend.STUB
    assert settings.redis_url is None


def test_a_real_backend_still_defaults_to_the_local_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spec's production default: embeddings are local, not remote."""
    settings = Settings(ai_backend=Backend.DOMESTIC, ai_base_url="https://api.domestic.ir/v1")
    assert settings.embed_backend is EmbedBackend.LOCAL


def test_a_commented_out_sentry_dsn_does_not_take_the_service_down(
    monkeypatch: pytest.MonkeyPatch, make_client: Callable[..., TestClient]
) -> None:
    """`SENTRY_DSN=   # empty disables Sentry` in an .env file reaches the
    container as that whole comment: compose does not strip it."""
    monkeypatch.setenv("SENTRY_DSN", "# empty disables Sentry")
    get_settings.cache_clear()

    client = make_client()

    assert client.get("/health").status_code == 200
