# 03 — `ai` service

**Build order: THIRD.** After platform and contracts. Parallel with `crawler`.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`.
**You own:** `services/ai/` and nothing else.

---

## What this service is

A **stateless HTTP service** providing three capabilities to the rest of the system: structured field extraction from Persian text, pairwise "are these the same part" adjudication, and text embeddings.

It is the **only** place in `yadakchi` that talks to a language model. No other service may import a model SDK.

**Stack: FastAPI.** This is the one service that is not Django, because it has no database at all and carries heavy ML dependencies that nothing else should inherit.

---

## How it connects

**This service consumes no Kafka topics and produces none.** It is purely request/response.

| Direction | Peer | Protocol |
|---|---|---|
| inbound | `enricher` | HTTP — `POST /v1/extract` |
| inbound | `matcher` | HTTP — `POST /v1/judge`, `POST /v1/embed` |
| inbound | `search` | HTTP — `POST /v1/embed` |
| outbound | model provider | HTTP, OpenAI-compatible |
| outbound | Redis (db 0) | cache |

You must publish an **OpenAPI document** at `/openapi.json` and commit a copy to `services/ai/contracts/published/openapi.json`. Consumers vendor it.

---

## API

### `POST /v1/extract`

Request: `{ "text": str, "schema_name": str, "hint": str|null }`
Response: `{ "fields": object, "confidences": {field: float}, "model": str, "cached": bool }`

`schema_name` selects one of the registered output schemas. Register `offer_fields` initially, matching the fields `enricher` needs: brand, part_number, part_type, authenticity_claim, pack_quantity, vehicle_hints.

- Prompt the model to return **JSON only**; strip markdown fences before parsing.
- Validate against the registered Pydantic schema. On failure, retry **once** with the validation error appended, then return HTTP 422.
- A field the model did not produce is `null` with confidence `0.0`. Never fabricate.

### `POST /v1/judge`

Request: `{ "a": str, "b": str, "context": object|null }`
Response: `{ "is_same": bool, "confidence": float, "reason_fa": str, "cached": bool }`

`reason_fa` is a short Persian explanation shown to human reviewers in `ops`. It is required, not optional.

The prompt must state explicitly:
- Different brands are **never** the same part.
- Different authenticity grades of the same design **are** different products here.
- Different pack quantities are different products.
- Different vehicle applicability is a strong negative signal but not decisive alone.

### `POST /v1/embed`

Request: `{ "texts": [str] }` (max 256 per call)
Response: `{ "vectors": [[float]], "dim": 384, "model": str }`

**Exactly 384 dimensions.** A configured model with a different dimension is a startup error, not a runtime surprise — fail loudly at boot. Never pad or truncate.

### `GET /health`, `GET /metrics`

Health reports backend, model, reachability, and today's budget usage.

---

## Backends

Selected by `AI_BACKEND`:

| Value | Meaning |
|---|---|
| `stub` | **Default in tests and CI.** Deterministic fake output, zero network |
| `local` | OpenAI-compatible endpoint served by Ollama or vLLM on the same host — **the production assumption** |
| `domestic` | OpenAI-compatible endpoint, different base URL and key |
| `external` | OpenAI-compatible endpoint |

All three real backends speak the same wire shape, so write **one HTTP client** parameterized by `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL`. Do not write three clients.

**Embeddings default to a locally loaded multilingual sentence-transformer model (384-dim, CPU-capable), regardless of `AI_BACKEND`.** Volume is high and depending on a remote service for it is unnecessary risk. Allow `AI_EMBED_BACKEND=http` as an override.

---

## Required behaviours

**Caching — mandatory.** Key on `sha256(backend + model + prompt_version + operation + input)`. Redis with a 30-day TTL plus an in-process LRU. A repeated title must never hit the model twice; duplicate titles are extremely common across sellers. Expose hit rate as a metric.

**Budget guard — mandatory.** Track spend (or wall-clock seconds for `local`) per day against `AI_DAILY_BUDGET`. At 80%, warn. At 100%, return **HTTP 429 with a `budget_exhausted` code** — do not silently degrade. `enricher` is designed to catch this and fall back to rules-only.

**Prompts** live in `prompts/*.txt` with a `PROMPT_VERSION` constant. Changing a prompt requires bumping the version, which invalidates the cache. Include at least six few-shot examples of real Persian spare-parts titles: a genuine part with an OEM code, an aftermarket part, a multi-vehicle listing, a listing with a phone number in the title, one with mixed Persian/Latin digits, one with no brand.

**Reliability.** Per-call timeout (default 60s), retry with backoff on 5xx and timeouts (max 3), internal batching of embeddings in chunks of 32, and a concurrency cap so one caller cannot saturate the host.

**Observability.** `yadakchi_ai_calls_total{op,backend,status}`, `_duration_seconds{op}`, `_cache_hits_total{op}`, `_tokens_total{direction}`, `_budget_used_ratio`. Never log full prompt content above debug level — titles are business data.

---

## Project layout

```
services/ai/
├── Dockerfile  requirements.txt  docker-compose.yml  Makefile  README.md
├── contracts/published/openapi.json
├── src/ai/
│   ├── main.py  config.py  logging_.py  metrics.py
│   ├── backends/{base,http,stub}.py
│   ├── embeddings.py  cache.py  budget.py
│   ├── schemas/offer_fields.py
│   └── prompts/{system,extract_offer_fields,judge_same_part}.txt
└── tests/
```

Your `docker-compose.yml` brings up **only** this service plus Redis. No Postgres, no Kafka.

---

## Acceptance criteria

1. With `AI_BACKEND=stub`, every endpoint works with **zero network access** and the whole suite runs offline.
2. Switching between `local`, `domestic`, and `external` changes only the base URL — verified against a mocked HTTP server.
3. `/v1/embed` returns exactly 384 dimensions; a mismatched configured model fails at **startup**.
4. Identical `/v1/extract` input twice produces one upstream call and one cache hit, proven by the metric.
5. Budget exhaustion returns 429 with `budget_exhausted`, and the ratio metric reads 1.0.
6. Malformed model JSON triggers exactly one repair retry, then 422.
7. Ten real Persian titles through `/v1/extract` extract brand and part number correctly for at least eight.
8. `/v1/judge` refuses to call two different-brand titles the same part, on a fixture set.
9. `openapi.json` in `contracts/published/` matches the live schema — enforced by a test.
10. `mypy`, `ruff`, tests pass.

## Explicitly out of scope

- Deciding **when** to call the model — that is `enricher`'s cascade and `matcher`'s ladder.
- Any business rule about parts, brands, or fitment.
- Any database. **This service is stateless.**

## Warnings

- Never let a caller pass a raw prompt. Only registered schema names and fixed prompts — otherwise prompt versioning and caching break.
- Never return a partially parsed object silently.
- Assume no GPU. Throughput will be modest; that is fine because every caller batches offline.
