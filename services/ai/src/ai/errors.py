"""The service's error vocabulary.

Every failure a caller can see is one of these, and every one of them renders
as the same JSON body::

    {"code": "budget_exhausted", "message": "...", "detail": {...}}

The codes are part of the contract. `enricher` keys its rules-only fallback on
`budget_exhausted`, so that string may never change without a version bump.
"""

from __future__ import annotations

from typing import Any


class AIServiceError(Exception):
    """Base class for every failure that maps onto an HTTP status."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def body(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload

    def log_fields(self) -> dict[str, Any]:
        """Same information, under keys `logging` does not already own.

        `LogRecord` reserves `message`, and passing it through `extra=` raises
        — a detail that turns an error path into a second, worse error.
        """
        return {"code": self.code, "error": self.message, "detail": self.detail}


class ConfigurationError(AIServiceError):
    """A setting is wrong. Raised at startup, never in a request.

    Deliberately fatal: a 384-dimension contract that is only discovered to be
    broken on the first call is worse than a container that refuses to boot.
    """

    status_code = 500
    code = "configuration_error"


class UnknownSchemaError(AIServiceError):
    """`schema_name` is not in the registry. Callers may not pass raw prompts."""

    status_code = 400
    code = "unknown_schema"


class ExtractionInvalidError(AIServiceError):
    """The model produced something that is not the registered schema, twice.

    One repair retry has already happened by the time this is raised. We never
    return a partially parsed object.
    """

    status_code = 422
    code = "extraction_invalid"


class JudgementInvalidError(AIServiceError):
    """The model could not produce a usable judgement, twice.

    Same rule as extraction: `matcher` gets a clear refusal and keeps the pair
    for a human, rather than a fabricated verdict.
    """

    status_code = 422
    code = "judgement_invalid"


class BudgetExhaustedError(AIServiceError):
    """Today's spend reached `AI_DAILY_BUDGET`.

    HTTP 429. We do not silently degrade — degrading is the caller's decision
    and `enricher` is built to make it.
    """

    status_code = 429
    code = "budget_exhausted"


class BackendError(AIServiceError):
    """The provider answered, but not usefully (4xx, or an unreadable body)."""

    status_code = 502
    code = "backend_error"


class BackendUnavailableError(AIServiceError):
    """The provider timed out or 5xx'd every attempt."""

    status_code = 503
    code = "backend_unavailable"


class InvalidRequestError(AIServiceError):
    """The request body failed validation."""

    status_code = 422
    code = "invalid_request"
