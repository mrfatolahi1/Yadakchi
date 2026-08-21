"""The three operations, and everything that happens around a model call.

Order matters here and is deliberate:

1. **cache** — an answer we already have costs nothing, so it is served before
   the budget is even consulted;
2. **budget** — a miss has to be paid for, and if today's budget is gone the
   caller gets 429 `budget_exhausted` rather than a quietly worse answer;
3. **call**, under a concurrency cap so one caller cannot saturate the host;
4. **validate**, with exactly one repair retry, then 422. A partially parsed
   object is never returned.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai.api_models import (
    EmbedRequest,
    EmbedResponse,
    ExtractRequest,
    ExtractResponse,
    JudgeRequest,
    JudgeResponse,
    contains_persian,
)
from ai.backends.base import Completion, CompletionRequest, ModelBackend
from ai.budget import BudgetGuard
from ai.cache import Cache, cache_key, canonical
from ai.config import EMBEDDING_DIM, Settings
from ai.embeddings import EmbeddingProvider
from ai.errors import ExtractionInvalidError, JudgementInvalidError, UnknownSchemaError
from ai.logging_ import get_logger, is_debug, text_preview
from ai.metrics import CALLS, DURATION, MODEL_INVOCATIONS, TOKENS, record_cache
from ai.prompts import (
    JUDGE_PROMPT_FILE,
    PROMPT_VERSION,
    load_prompt,
    render,
    system_prompt,
)
from ai.schemas import RegisteredSchema, get_schema, schema_names

logger = get_logger(__name__)

#: Embeddings have no prompt, so the "prompt version" slot of the cache key
#: carries a version of its own. Bumping a text prompt must not throw away
#: millions of vectors that are still perfectly valid.
EMBED_KEY_VERSION = "embed-v1"

_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")


class JudgeAnswer(BaseModel):
    """What the model must return for `/v1/judge`."""

    model_config = ConfigDict(extra="forbid")

    is_same: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason_fa: str = Field(min_length=1)

    @field_validator("reason_fa")
    @classmethod
    def _must_be_persian(cls, value: str) -> str:
        if not contains_persian(value):
            raise ValueError("reason_fa must be written in Persian: a reviewer in ops reads it")
        return value.strip()


@dataclass(frozen=True)
class ParsedAnswer:
    """A validated model answer plus what it cost to get it."""

    data: dict[str, Any]
    completion: Completion


def strip_fences(text: str) -> str:
    """Remove ```json fences. Small models add them however you ask."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE.sub("", stripped).strip()
    return stripped


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse one JSON object out of a model's answer, or raise ValueError."""
    candidate = strip_fences(text)
    try:
        value = json.loads(candidate)
    except ValueError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object found in the model's answer") from None
        value = json.loads(candidate[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}")
    return value


class AIService:
    """Everything the routes need. One instance per app."""

    def __init__(
        self,
        settings: Settings,
        *,
        backend: ModelBackend,
        embedder: EmbeddingProvider,
        cache: Cache,
        budget: BudgetGuard,
    ) -> None:
        self._settings = settings
        self._backend = backend
        self._embedder = embedder
        self._cache = cache
        self._budget = budget
        # One caller must not be able to occupy every worker on a CPU-only box.
        self._semaphore = asyncio.Semaphore(settings.ai_max_concurrency)

    @property
    def backend(self) -> ModelBackend:
        return self._backend

    @property
    def embedder(self) -> EmbeddingProvider:
        return self._embedder

    @property
    def cache(self) -> Cache:
        return self._cache

    @property
    def budget(self) -> BudgetGuard:
        return self._budget

    # ---------------------------------------------------------------- extract
    async def extract(self, request: ExtractRequest) -> ExtractResponse:
        op = "extract"
        schema = get_schema(request.schema_name)
        if schema is None:
            CALLS.labels(op=op, backend=self._backend.name, status="unknown_schema").inc()
            raise UnknownSchemaError(
                f"unknown schema_name {request.schema_name!r}; callers select a registered "
                "schema by name and may never pass a prompt",
                {"schema_name": request.schema_name, "registered": list(schema_names())},
            )

        key = self._key(
            op,
            canonical(
                {"schema": schema.name, "text": request.text, "hint": request.hint},
            ),
        )
        started = time.perf_counter()
        cached = await self._cache.get(key)
        record_cache(op, hit=cached is not None)
        if cached is not None:
            CALLS.labels(op=op, backend=self._backend.name, status="cached").inc()
            DURATION.labels(op=op).observe(time.perf_counter() - started)
            return ExtractResponse(
                fields=cached["fields"],
                confidences=cached["confidences"],
                model=cached["model"],
                cached=True,
            )

        await self._budget.check()

        hint_block = f"CONTEXT: {request.hint}\n" if request.hint else ""
        user_prompt = render(
            load_prompt(schema.prompt_file),
            text=request.text,
            hint=hint_block,
        )
        completion_request = CompletionRequest(
            op=op,
            system=system_prompt(),
            user=user_prompt,
            max_tokens=self._settings.ai_max_output_tokens,
            temperature=self._settings.ai_temperature,
            payload={
                "text": request.text,
                "hint": request.hint,
                "schema_name": schema.name,
            },
        )

        answer = await self._ask_with_repair(
            completion_request,
            validate=lambda data: _validate_extraction(schema, data),
            invalid=lambda message, attempts: ExtractionInvalidError(
                "the model did not return the requested schema after one repair retry",
                {"schema_name": schema.name, "error": message, "attempts": attempts},
            ),
        )

        fields, confidences = _validate_extraction(schema, answer.data)
        payload: dict[str, Any] = {
            "fields": fields,
            "confidences": confidences,
            "model": answer.completion.model,
        }
        await self._cache.set(key, payload)
        CALLS.labels(op=op, backend=self._backend.name, status="ok").inc()
        DURATION.labels(op=op).observe(time.perf_counter() - started)
        return ExtractResponse(
            fields=fields,
            confidences=confidences,
            model=answer.completion.model,
            cached=False,
        )

    # ------------------------------------------------------------------ judge
    async def judge(self, request: JudgeRequest) -> JudgeResponse:
        op = "judge"
        key = self._key(
            op,
            canonical({"a": request.a, "b": request.b, "context": request.context}),
        )
        started = time.perf_counter()
        cached = await self._cache.get(key)
        record_cache(op, hit=cached is not None)
        if cached is not None:
            CALLS.labels(op=op, backend=self._backend.name, status="cached").inc()
            DURATION.labels(op=op).observe(time.perf_counter() - started)
            return JudgeResponse(
                is_same=bool(cached["is_same"]),
                confidence=float(cached["confidence"]),
                reason_fa=str(cached["reason_fa"]),
                cached=True,
            )

        await self._budget.check()

        context_block = ""
        if request.context:
            context_block = f"CONTEXT: {canonical(request.context)}\n"
        completion_request = CompletionRequest(
            op=op,
            system=system_prompt(),
            user=render(
                load_prompt(JUDGE_PROMPT_FILE),
                a=request.a,
                b=request.b,
                context=context_block,
            ),
            max_tokens=self._settings.ai_max_output_tokens,
            temperature=self._settings.ai_temperature,
            payload={"a": request.a, "b": request.b, "context": request.context},
        )

        answer = await self._ask_with_repair(
            completion_request,
            validate=lambda data: JudgeAnswer.model_validate(data).model_dump(),
            invalid=lambda message, attempts: JudgementInvalidError(
                "the model did not return a usable judgement after one repair retry",
                {"error": message, "attempts": attempts},
            ),
        )

        judgement = JudgeAnswer.model_validate(answer.data)
        await self._cache.set(key, judgement.model_dump())
        CALLS.labels(op=op, backend=self._backend.name, status="ok").inc()
        DURATION.labels(op=op).observe(time.perf_counter() - started)
        return JudgeResponse(
            is_same=judgement.is_same,
            confidence=judgement.confidence,
            reason_fa=judgement.reason_fa,
            cached=False,
        )

    # ------------------------------------------------------------------ embed
    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        op = "embed"
        started = time.perf_counter()
        model_id = self._embedder.model_id

        # Duplicate titles are the norm: dedupe inside the request too, not
        # just across requests.
        unique: dict[str, None] = dict.fromkeys(request.texts)
        keys = {text: self._embed_key(text) for text in unique}
        vectors: dict[str, list[float]] = {}

        for text, key in keys.items():
            cached = await self._cache.get(key)
            hit = cached is not None and isinstance(cached.get("vector"), list)
            record_cache(op, hit=hit)
            if cached is not None and hit:
                vectors[text] = [float(value) for value in cached["vector"]]

        missing = [text for text in unique if text not in vectors]
        if missing:
            if self._embedder.name == "http":
                # A local model costs no money and no provider quota; a remote
                # one does, so only that one is metered.
                await self._budget.check()
            batch_size = self._settings.ai_embed_batch_size
            elapsed = 0.0
            for start in range(0, len(missing), batch_size):
                chunk = missing[start : start + batch_size]
                chunk_started = time.perf_counter()
                async with self._semaphore:
                    encoded = await self._embedder.encode(chunk)
                elapsed += time.perf_counter() - chunk_started
                MODEL_INVOCATIONS.labels(op=op, backend=self._embedder.name).inc()
                for text, vector in zip(chunk, encoded, strict=True):
                    vectors[text] = vector
                    await self._cache.set(keys[text], {"vector": vector, "model": model_id})
            if self._embedder.name == "http":
                await self._budget.record(
                    self._budget.cost_of(prompt_tokens=0, completion_tokens=0, seconds=elapsed)
                )

        ordered = [vectors[text] for text in request.texts]
        for vector in ordered:
            if len(vector) != EMBEDDING_DIM:  # pragma: no cover - providers verify too
                raise AssertionError(f"embedding width drifted to {len(vector)}")
        CALLS.labels(op=op, backend=self._embedder.name, status="ok").inc()
        DURATION.labels(op=op).observe(time.perf_counter() - started)
        return EmbedResponse(vectors=ordered, model=model_id)

    # -------------------------------------------------------------- internals
    def _key(self, op: str, payload: str) -> str:
        return cache_key(
            backend=self._backend.name,
            model=self._backend.model_id,
            prompt_version=PROMPT_VERSION,
            operation=op,
            payload=payload,
        )

    def _embed_key(self, text: str) -> str:
        return cache_key(
            backend=self._embedder.name,
            model=self._embedder.model_id,
            prompt_version=EMBED_KEY_VERSION,
            operation="embed",
            payload=text,
        )

    async def _call(self, request: CompletionRequest) -> Completion:
        """One model invocation: concurrency-capped, metered, never logged raw."""
        if is_debug(logger):
            logger.debug(
                "model prompt",
                extra={"op": request.op, "system": request.system, "user": request.user},
            )
        else:
            logger.info(
                "model call",
                extra={
                    "op": request.op,
                    "backend": self._backend.name,
                    "model": self._backend.model_id,
                    "prompt": text_preview(request.user),
                },
            )
        async with self._semaphore:
            completion = await self._backend.complete(request)
        MODEL_INVOCATIONS.labels(op=request.op, backend=self._backend.name).inc()
        TOKENS.labels(direction="prompt").inc(completion.prompt_tokens)
        TOKENS.labels(direction="completion").inc(completion.completion_tokens)
        await self._budget.record(
            self._budget.cost_of(
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
                seconds=completion.duration_seconds,
            )
        )
        return completion

    async def _ask_with_repair(
        self,
        request: CompletionRequest,
        *,
        validate: Any,
        invalid: Any,
    ) -> ParsedAnswer:
        """Ask once; on a bad answer, ask again with the error, then give up.

        Exactly one repair retry — no more. A model that cannot produce the
        schema twice will not produce it on the fifth attempt either, and the
        caller is waiting.
        """
        completion = await self._call(request)
        try:
            data = parse_json_object(completion.text)
            validate(data)
        except (ValueError, ValidationError) as first_error:
            message = _error_message(first_error)
            logger.warning(
                "model answer failed validation, asking once for a repair",
                extra={
                    "op": request.op,
                    "backend": self._backend.name,
                    "error": message,
                    "answer": text_preview(completion.text),
                },
            )
            repair = CompletionRequest(
                op=request.op,
                system=request.system,
                user=(
                    f"{request.user}\n\n"
                    "Your previous answer was rejected. Fix it and answer again with a "
                    "single valid JSON object and nothing else.\n"
                    f"Previous answer: {completion.text}\n"
                    f"Validation error: {message}"
                ),
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                payload=request.payload,
            )
            completion = await self._call(repair)
            try:
                data = parse_json_object(completion.text)
                validate(data)
            except (ValueError, ValidationError) as second_error:
                CALLS.labels(op=request.op, backend=self._backend.name, status="invalid").inc()
                raise invalid(_error_message(second_error), 2) from second_error

        return ParsedAnswer(data=data, completion=completion)


def _error_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        parts = [
            f"{'.'.join(str(item) for item in issue['loc']) or '<root>'}: {issue['msg']}"
            for issue in error.errors()
        ]
        return "; ".join(parts)
    return str(error)


def _validate_extraction(
    schema: RegisteredSchema, data: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, float]]:
    """Validate the model's envelope and normalise it into the response shape.

    * every registered field is present in the answer, null when absent;
    * a field with no value has confidence 0.0, whatever the model claimed;
    * confidences are clamped into [0, 1] and unknown keys are dropped, which
      is leniency the *values* never get.
    """
    if "fields" not in data:
        raise ValueError("the answer has no 'fields' object")
    raw_fields = data["fields"]
    if not isinstance(raw_fields, dict):
        raise ValueError("'fields' must be an object")

    validated = schema.model.model_validate(raw_fields)
    fields = validated.model_dump(mode="json")

    raw_confidences = data.get("confidences") or {}
    if not isinstance(raw_confidences, dict):
        raise ValueError("'confidences' must be an object")

    confidences: dict[str, float] = {}
    for name in schema.field_names:
        value = fields.get(name)
        if value is None or value == [] or value == "":
            confidences[name] = 0.0
            continue
        try:
            claimed = float(raw_confidences.get(name, 0.5))
        except (TypeError, ValueError):
            claimed = 0.5
        confidences[name] = max(0.0, min(1.0, claimed))
    return fields, confidences
