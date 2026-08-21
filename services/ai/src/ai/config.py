"""Settings, and the startup checks that make a misconfiguration fatal.

Two rules shape this file:

* **`stub` is the default.** Ten services share one test suite and none of them
  has network access in CI, so a clone with no environment at all must boot,
  answer every endpoint and run every test.
* **A wrong setting fails at boot, not on the first call.** The 384-dimension
  embedding contract in particular: `matcher` writes those vectors into a
  pgvector column and `search` into Typesense, and neither can recover from a
  service that quietly starts returning 768 floats.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Final

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.errors import ConfigurationError

#: The one number the rest of the system hard-codes against. Never padded,
#: never truncated: a model of a different width is a configuration error.
EMBEDDING_DIM: Final[int] = 384

#: Contract constant, not a setting — it appears in the published OpenAPI
#: document as a schema constraint, so it may not vary per deployment.
MAX_EMBED_TEXTS: Final[int] = 256

#: Widths we know without asking. Enough to reject the popular wrong choices
#: (multilingual-e5-base, bge-m3, the OpenAI models) before a socket is opened,
#: which is what makes the check work with zero network in `stub` mode. A model
#: that is not listed is verified for real when it loads, still at startup.
KNOWN_EMBEDDING_DIMS: Final[dict[str, int]] = {
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "sentence-transformers/all-MiniLM-L12-v2": 384,
    "intfloat/multilingual-e5-small": 384,
    "BAAI/bge-small-en-v1.5": 384,
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": 768,
    "sentence-transformers/all-mpnet-base-v2": 768,
    "intfloat/multilingual-e5-base": 768,
    "nomic-embed-text": 768,
    "intfloat/multilingual-e5-large": 1024,
    "mxbai-embed-large": 1024,
    "BAAI/bge-m3": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class Backend(StrEnum):
    """Which model provider answers `/v1/extract` and `/v1/judge`."""

    STUB = "stub"
    LOCAL = "local"
    DOMESTIC = "domestic"
    EXTERNAL = "external"

    @property
    def is_remote(self) -> bool:
        return self is not Backend.STUB


class EmbedBackend(StrEnum):
    """Where `/v1/embed` gets its vectors from."""

    STUB = "stub"
    LOCAL = "local"
    HTTP = "http"


#: Default base URL per backend. `local` is an Ollama or vLLM process on the
#: same host — the production assumption — so it has a sensible default; the
#: other two are deployment-specific and must be given.
DEFAULT_BASE_URLS: Final[dict[Backend, str | None]] = {
    Backend.STUB: None,
    Backend.LOCAL: "http://localhost:11434/v1",
    Backend.DOMESTIC: None,
    Backend.EXTERNAL: None,
}


class Settings(BaseSettings):
    """Environment. No secret ever appears in code or in a log line."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        # `model_` is Pydantic's own namespace; ours are ai_model / ai_backend.
        protected_namespaces=(),
    )

    # --- provider ---------------------------------------------------------
    ai_backend: Backend = Backend.STUB
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_model: str = "qwen2.5:7b-instruct"

    # Ask for a JSON object explicitly where the provider supports it. Ollama
    # and vLLM both map `response_format` onto constrained decoding; an
    # endpoint that rejects the field can turn it off without touching code.
    ai_json_mode: bool = True
    ai_temperature: float = 0.0
    ai_max_output_tokens: int = 768

    # --- reliability ------------------------------------------------------
    ai_timeout_seconds: float = 60.0
    #: Total attempts per call, not retries on top of one. 3 means one call and
    #: two retries, with exponential backoff between them.
    ai_max_attempts: int = Field(default=3, ge=1, le=10)
    ai_retry_backoff_seconds: float = 0.5
    #: One caller must not be able to saturate a CPU-only host.
    ai_max_concurrency: int = Field(default=8, ge=1)

    # --- embeddings -------------------------------------------------------
    #: Unset means: `local` (a sentence-transformer loaded in this process) for
    #: every real backend, and `stub` when AI_BACKEND=stub so that an offline
    #: clone still answers /v1/embed. Set it explicitly to override either way.
    ai_embed_backend: EmbedBackend | None = None
    ai_embed_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    ai_embed_base_url: str | None = None
    ai_embed_api_key: str | None = None
    #: Where `sentence-transformers` looks for weights. Baked into the image at
    #: build time so a container needs no network at boot.
    ai_embed_model_path: str | None = None
    ai_embed_batch_size: int = Field(default=32, ge=1, le=256)

    # --- cache ------------------------------------------------------------
    #: Redis db 8, from platform/.env as AI_REDIS_URL. Absent means the
    #: in-process LRU carries the cache alone — degraded, never fatal.
    redis_url: str | None = None
    ai_cache_ttl_seconds: int = Field(default=30 * 24 * 3600, ge=1)
    ai_cache_lru_size: int = Field(default=4096, ge=1)
    ai_cache_enabled: bool = True

    # --- budget -----------------------------------------------------------
    ai_budget_enabled: bool = True
    #: Wall-clock seconds per UTC day for `local`/`stub`; currency units per
    #: day for a metered provider (see `ai_cost_per_1k_*`).
    ai_daily_budget: float = 3600.0
    ai_cost_per_1k_input: float = 0.0
    ai_cost_per_1k_output: float = 0.0

    # --- operational ------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "json"
    sentry_dsn: str | None = None
    sentry_environment: str = "local"
    ai_health_probe_ttl_seconds: float = 15.0

    # ----------------------------------------------------------------- derived
    @property
    def embed_backend(self) -> EmbedBackend:
        """Resolve the embedding backend, honouring an explicit override.

        The spec's production default is a locally loaded sentence-transformer
        regardless of AI_BACKEND. That model needs weights on disk, which an
        offline CI runner does not have, so `stub` keeps its promise of zero
        network by also stubbing embeddings — unless AI_EMBED_BACKEND says
        otherwise, which is how a developer runs the real embedder locally.
        """
        if self.ai_embed_backend is not None:
            return self.ai_embed_backend
        return EmbedBackend.STUB if self.ai_backend is Backend.STUB else EmbedBackend.LOCAL

    @property
    def base_url(self) -> str | None:
        return self.ai_base_url or DEFAULT_BASE_URLS[self.ai_backend]

    @property
    def embed_base_url(self) -> str | None:
        return self.ai_embed_base_url or self.base_url

    @property
    def embed_api_key(self) -> str | None:
        return self.ai_embed_api_key or self.ai_api_key

    @property
    def budget_unit(self) -> str:
        """What `AI_DAILY_BUDGET` counts.

        A metered provider is billed per token, so we count money. A local
        model costs the host's time, so we count seconds — the only scarce
        resource on a CPU-only box.
        """
        priced = self.ai_cost_per_1k_input > 0 or self.ai_cost_per_1k_output > 0
        return "currency" if priced else "seconds"

    # -------------------------------------------------------------- validation
    @field_validator(
        "ai_base_url",
        "ai_api_key",
        "ai_embed_backend",
        "ai_embed_base_url",
        "ai_embed_api_key",
        "ai_embed_model_path",
        "redis_url",
        "sentry_dsn",
        mode="before",
    )
    @classmethod
    def _empty_string_means_unset(cls, value: object) -> object:
        """`docker compose` passes an unset variable through as "".

        Without this, `AI_EMBED_BACKEND: ${AI_EMBED_BACKEND:-}` in a compose
        file is an empty string, which is not a valid enum member, and the
        container refuses to start for no reason a reader would guess.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        self.check_embedding_model()
        self.check_backend()
        self.check_budget()
        return self

    def check_backend(self) -> None:
        if self.ai_backend.is_remote and not self.base_url:
            raise ConfigurationError(
                f"AI_BACKEND={self.ai_backend.value} needs AI_BASE_URL "
                "(an OpenAI-compatible endpoint, e.g. https://host/v1)",
                {"setting": "AI_BASE_URL"},
            )
        if self.embed_backend is EmbedBackend.HTTP and not self.embed_base_url:
            raise ConfigurationError(
                "AI_EMBED_BACKEND=http needs AI_EMBED_BASE_URL or AI_BASE_URL",
                {"setting": "AI_EMBED_BASE_URL"},
            )

    def check_budget(self) -> None:
        if self.ai_budget_enabled and self.ai_daily_budget <= 0:
            raise ConfigurationError(
                "AI_DAILY_BUDGET must be greater than zero while the budget guard "
                "is enabled; set AI_BUDGET_ENABLED=false to run unmetered",
                {"setting": "AI_DAILY_BUDGET", "value": self.ai_daily_budget},
            )

    def check_embedding_model(self) -> None:
        """Reject a known-wrong embedding model before anything opens a socket.

        A model we do not have on this list is not accepted on trust: it is
        measured when the provider loads, which also happens at startup.
        """
        if self.embed_backend is EmbedBackend.STUB:
            return
        known = KNOWN_EMBEDDING_DIMS.get(self.ai_embed_model)
        if known is not None and known != EMBEDDING_DIM:
            raise ConfigurationError(
                f"AI_EMBED_MODEL={self.ai_embed_model!r} produces {known}-dimension "
                f"vectors; this service is contracted to {EMBEDDING_DIM}. Vectors are "
                "never padded or truncated — choose a 384-dimension model.",
                {
                    "setting": "AI_EMBED_MODEL",
                    "configured_dim": known,
                    "required_dim": EMBEDDING_DIM,
                },
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. `get_settings.cache_clear()` in tests."""
    return Settings()
