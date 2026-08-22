from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field


class EmbeddingError(RuntimeError):
    pass


class EmbedResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    vectors: list[list[float]]
    dim: int = Field(default=384)
    model: str


class EmbeddingClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class AiClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not 1 <= len(texts) <= 256:
            raise ValueError("the AI embedding endpoint accepts 1 to 256 texts")
        try:
            response = httpx.post(
                f"{self._base_url}/v1/embed",
                json={"texts": texts},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingError("AI embedding request failed") from exc
        parsed = EmbedResponse.model_validate(response.json())
        if parsed.dim != 384 or len(parsed.vectors) != len(texts):
            raise EmbeddingError("AI returned an invalid embedding response shape")
        if any(len(vector) != 384 for vector in parsed.vectors):
            raise EmbeddingError("AI returned a vector whose dimension is not 384")
        return parsed.vectors
