"""Embeddings — exactly 384 dimensions, or the process does not start.

`matcher` stores these in a pgvector column and `search` in a Typesense field,
both declared as 384. A service that quietly began returning 768 floats would
not fail here; it would fail in two other services, at write time, after a
reprocess had already started. So the width is checked at startup — by table
for the models we know, and by measuring the loaded model for the ones we do
not — and every response is re-checked before it leaves. Vectors are never
padded and never truncated.

Three providers, one interface:

* `stub` — deterministic hashed character n-grams. No weights, no network,
  the default whenever AI_BACKEND=stub;
* `local` — a multilingual sentence-transformer loaded in this process, on
  CPU. The production default: embedding volume is high and making it depend
  on a remote service is unnecessary risk;
* `http` — the OpenAI-compatible `/embeddings` endpoint, for when someone
  really does want it remote.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from abc import ABC, abstractmethod
from typing import Any, Final

import httpx

from ai.backends.http import RETRYABLE_STATUSES, build_client
from ai.config import EMBEDDING_DIM, EmbedBackend, Settings
from ai.errors import BackendError, BackendUnavailableError, ConfigurationError
from ai.logging_ import get_logger
from ai.text import normalize

logger = get_logger(__name__)

#: Bumped when the stub's vector construction changes: it is part of the cache
#: key, so old vectors are never mixed with new ones.
STUB_EMBED_MODEL_ID: Final[str] = "stub-hashed-384-v1"


class EmbeddingProvider(ABC):
    """Turns text into vectors of exactly `EMBEDDING_DIM` floats."""

    name: str = "unknown"

    @property
    @abstractmethod
    def model_id(self) -> str: ...

    @abstractmethod
    async def encode(self, texts: list[str]) -> list[list[float]]:
        """Embed in input order. The caller has already deduplicated."""

    @abstractmethod
    async def startup_check(self) -> int:
        """Measure the real width. Raises ConfigurationError when it is wrong."""

    async def aclose(self) -> None:
        return None

    def _verify(self, vectors: list[list[float]]) -> list[list[float]]:
        for vector in vectors:
            if len(vector) != EMBEDDING_DIM:
                raise BackendError(
                    f"embedding provider returned {len(vector)} dimensions, "
                    f"expected {EMBEDDING_DIM}; vectors are never padded or truncated",
                    {"provider": self.name, "model": self.model_id},
                )
        return vectors


# --------------------------------------------------------------------- stub


def _hashed_vector(text: str) -> list[float]:
    """A deterministic 384-dimension vector with a little semantics in it.

    Character 4-grams and whole words are hashed into signed buckets, then L2
    normalised. Two titles that share words land near each other, which is
    enough for `matcher` and `search` to exercise their vector paths offline.
    It is not a language model and does not pretend to be one.
    """
    vector = [0.0] * EMBEDDING_DIM
    cleaned = normalize(text).lower()
    features: list[str] = [f"w:{word}" for word in cleaned.split()]
    padded = f"  {cleaned}  "
    features += [f"c:{padded[i : i + 4]}" for i in range(max(0, len(padded) - 3))]

    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
        vector[index] += 1.0 if digest[4] & 1 else -1.0

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        # Empty or whitespace-only input still gets a stable unit vector.
        seed = hashlib.blake2b(text.encode("utf-8"), digest_size=4).digest()
        vector[int.from_bytes(seed, "big") % EMBEDDING_DIM] = 1.0
        return vector
    return [value / norm for value in vector]


class StubEmbedder(EmbeddingProvider):
    name = "stub"

    @property
    def model_id(self) -> str:
        return STUB_EMBED_MODEL_ID

    async def encode(self, texts: list[str]) -> list[list[float]]:
        return self._verify([_hashed_vector(text) for text in texts])

    async def startup_check(self) -> int:
        return EMBEDDING_DIM


# -------------------------------------------------------------------- local


class LocalEmbedder(EmbeddingProvider):
    """A sentence-transformer in this process, on CPU.

    `sentence-transformers` is an optional install (requirements-embed.txt):
    it drags in torch, and a clone that only ever runs the stub should not pay
    two gigabytes for it. Asking for this provider without it installed is a
    configuration error, raised at startup like every other one.
    """

    name = "local"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model_name = settings.ai_embed_model
        self._model: Any = None

    @property
    def model_id(self) -> str:
        return self._model_name

    def _load(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ConfigurationError(
                "AI_EMBED_BACKEND=local needs sentence-transformers: "
                "`pip install -r requirements-embed.txt`, or build the image with "
                "--build-arg WITH_LOCAL_EMBEDDINGS=1",
                {"model": self._model_name},
            ) from exc
        try:
            return SentenceTransformer(
                self._settings.ai_embed_model_path or self._model_name,
                device="cpu",
            )
        except Exception as exc:  # pragma: no cover - depends on the install
            raise ConfigurationError(
                f"could not load the embedding model {self._model_name!r}: {exc}",
                {"model": self._model_name},
            ) from exc

    async def startup_check(self) -> int:
        loop = asyncio.get_running_loop()
        self._model = await loop.run_in_executor(None, self._load)
        dimension = int(self._model.get_sentence_embedding_dimension())
        if dimension != EMBEDDING_DIM:
            raise ConfigurationError(
                f"AI_EMBED_MODEL={self._model_name!r} loaded with {dimension} dimensions; "
                f"this service is contracted to {EMBEDDING_DIM}",
                {"configured_dim": dimension, "required_dim": EMBEDDING_DIM},
            )
        logger.info(
            "local embedding model ready",
            extra={"model": self._model_name, "dim": dimension},
        )
        return dimension

    async def encode(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:  # pragma: no cover - lifespan loads it first
            await self.startup_check()
        loop = asyncio.get_running_loop()
        vectors = await loop.run_in_executor(None, self._encode_sync, texts)
        return self._verify(vectors)

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        raw = self._model.encode(
            texts,
            batch_size=self._settings.ai_embed_batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in row] for row in raw]


# --------------------------------------------------------------------- http


class HttpEmbedder(EmbeddingProvider):
    """OpenAI-compatible `POST /embeddings`, same client shape as the chat one."""

    name = "http"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = settings.embed_base_url
        if not base_url:  # pragma: no cover - Settings validation rejects this first
            raise ConfigurationError("AI_EMBED_BACKEND=http needs AI_EMBED_BASE_URL")
        self._settings = settings
        self._model_name = settings.ai_embed_model
        self._client = build_client(
            base_url=base_url,
            api_key=settings.embed_api_key,
            timeout=settings.ai_timeout_seconds,
            transport=transport,
        )

    @property
    def model_id(self) -> str:
        return self._model_name

    async def encode(self, texts: list[str]) -> list[list[float]]:
        attempts = self._settings.ai_max_attempts
        backoff = self._settings.ai_retry_backoff_seconds
        last_error = "no attempt was made"
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.post(
                    "embeddings",
                    json={"model": self._model_name, "input": texts},
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code in RETRYABLE_STATUSES:
                    last_error = f"HTTP {response.status_code}"
                elif response.status_code >= 400:
                    raise BackendError(
                        f"embedding provider rejected the request with HTTP {response.status_code}",
                        {"status": response.status_code},
                    )
                else:
                    return self._verify(self._parse(response, len(texts)))
            if attempt < attempts:
                await asyncio.sleep(backoff * (2 ** (attempt - 1)))
        raise BackendUnavailableError(
            f"embedding provider unreachable after {attempts} attempt(s): {last_error}",
            {"attempts": attempts},
        )

    def _parse(self, response: httpx.Response, expected: int) -> list[list[float]]:
        try:
            data = response.json()
            rows = sorted(data["data"], key=lambda row: int(row.get("index", 0)))
            vectors = [[float(value) for value in row["embedding"]] for row in rows]
        except (ValueError, KeyError, TypeError) as exc:
            raise BackendError(
                "embedding provider returned a body that is not an OpenAI embeddings response",
                {"error": str(exc)},
            ) from exc
        if len(vectors) != expected:
            raise BackendError(
                f"embedding provider returned {len(vectors)} vectors for {expected} texts",
                {"expected": expected, "received": len(vectors)},
            )
        return vectors

    async def startup_check(self) -> int:
        """Ask the endpoint for one vector and measure it, before serving.

        A width mismatch is a configuration error even though it arrives over
        HTTP: the deployment named a model that cannot honour the contract,
        and finding that out at boot is the whole point.
        """
        try:
            vectors = await self.encode(["سلام"])
        except BackendError as exc:
            raise ConfigurationError(
                f"the configured embedding endpoint cannot serve {EMBEDDING_DIM}-dimension "
                f"vectors: {exc.message}",
                {"model": self._model_name, "required_dim": EMBEDDING_DIM},
            ) from exc
        return len(vectors[0])

    async def aclose(self) -> None:
        await self._client.aclose()


def build_embedder(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EmbeddingProvider:
    match settings.embed_backend:
        case EmbedBackend.STUB:
            return StubEmbedder()
        case EmbedBackend.LOCAL:
            return LocalEmbedder(settings)
        case EmbedBackend.HTTP:
            return HttpEmbedder(settings, transport=transport)
