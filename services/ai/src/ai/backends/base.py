"""What a backend is, from the service's point of view.

Everything above this line — caching, the budget guard, validation, the repair
retry — is written once and works the same whether the answer came from a
7B model on the host or from the deterministic stub. That is the whole reason
the stub is not an afterthought: it is the same interface, so the offline path
is the tested path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CompletionRequest:
    """One rendered prompt, plus the structured inputs that produced it.

    `payload` is what the stub reads. A real provider never sees it — it gets
    `system` and `user`, which is all an OpenAI-compatible endpoint accepts.
    Keeping both means the prompt is rendered (and therefore version-checked
    and cached) on exactly the same path in both modes.
    """

    op: str
    system: str
    user: str
    max_tokens: int
    temperature: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Completion:
    """A model's raw answer. Text, not an object: parsing happens upstream."""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_seconds: float = 0.0


class ModelBackend(ABC):
    """A source of completions."""

    #: `stub` | `local` | `domestic` | `external` — the metric label.
    name: str = "unknown"

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Identifier reported to callers and mixed into every cache key."""

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> Completion:
        """Answer one prompt, or raise BackendError/BackendUnavailableError."""

    @abstractmethod
    async def reachable(self) -> bool:
        """Can this backend be reached right now? Reported by /health."""

    async def aclose(self) -> None:
        """Release transport resources. Safe to call more than once."""
        return None
