# ai

FastAPI inference service — field extraction, pair adjudication and embeddings
for the rest of the system. It has no database.

**Your instructions are in this folder.** [`BRIEF.md`](./BRIEF.md) is the shared
project brief — read it first, every time — and [`SPEC.md`](./SPEC.md) is the
full specification for this service, including its acceptance criteria. Together
they are everything you need: you own `services/ai/` and nothing else, you
never read or modify another service's folder or database, and you talk to other
services only over Kafka (plus the few synchronous HTTP pairs the brief allows).
If something you need is missing from a spec, stop and report it rather than
inventing it.

Both files are copies, distributed from `docs/specs/` by `make sync-specs`.
**Do not edit them here** — CI compares them byte-for-byte against the source and
will fail the build. Spec changes happen in `docs/specs/`, reviewed by a human.

---

## Quick start

```bash
make install      # virtualenv + dev dependencies
make check        # lint, types, tests — offline, no containers
make run          # http://127.0.0.1:8000/docs
```

Containers, this service plus Redis and nothing else:

```bash
make up           # build and start
make smoke        # call every endpoint
make docker-test  # the suite, inside the image
make down
```

## What it does

Three capabilities, and no way for a caller to ask for anything else:

| Endpoint | Called by | Answers |
|---|---|---|
| `POST /v1/extract` | `enricher` | structured fields from a Persian listing title |
| `POST /v1/judge` | `matcher` | are these two listings the same product? |
| `POST /v1/embed` | `matcher`, `search` | 384-dimension vectors |
| `GET /health` | operators | backend, model, reachability, today's budget |
| `GET /metrics` | Prometheus | the counters below |

A caller selects an output shape by **name** (`schema_name`), never by sending
a prompt or a schema of its own. Prompt versioning and the cache key both
depend on the prompt being fixed here.

Errors all share one body — `{"code", "message", "detail"}`. The code that
matters most is **`budget_exhausted`** (HTTP 429): today's model budget is
spent and the caller should fall back to its own rules. `enricher` is built
for exactly that.

## The contract

`contracts/published/openapi.json` is the published OpenAPI document.
`enricher`, `matcher` and `search` vendor it and generate clients from it.

```bash
make openapi      # regenerate after a deliberate change, then commit
```

`tests/test_openapi_contract.py` fails the build if the committed copy and the
live schema disagree, so the file cannot silently rot.

## Backends

`AI_BACKEND` selects the provider. There is **one** HTTP client; `local`,
`domestic` and `external` differ by nothing but `AI_BASE_URL`, and a test
compares the three request-for-request to keep it that way.

| Value | Meaning |
|---|---|
| `stub` | **The default.** Deterministic, offline, no weights, no network |
| `local` | OpenAI-compatible endpoint on the host (Ollama, vLLM) — the production assumption |
| `domestic` | OpenAI-compatible endpoint, different base URL and key |
| `external` | OpenAI-compatible endpoint |

### The stub is not a mock

`AI_BACKEND=stub` is the default because the whole system's test suite — ten
services, none with network access in CI — runs against it. So the stub is a
small rule engine over a spare-parts lexicon rather than a fixed answer: it
normalises Persian and Arabic digits, folds letter variants, throws away phone
numbers and prices before looking for a part number, and applies the same four
adjudication rules the real prompt states. On the ten real titles in
`tests/fixtures/titles.py` it reads brand and part number correctly on all ten;
the acceptance criterion asks for eight.

It returns a JSON *string*, exactly as a model would, so the strip-parse-
validate-repair path is exercised offline as well.

## Embeddings

Always exactly **384** dimensions, never padded, never truncated. `matcher`
stores them in a pgvector column and `search` in a Typesense field, both
declared 384, so a mismatch is a configuration error that stops the process at
boot — by table for the models we know, and by measuring the model when it
loads for the ones we do not.

| `AI_EMBED_BACKEND` | Meaning |
|---|---|
| unset | `local` for a real backend; `stub` when `AI_BACKEND=stub` |
| `local` | a multilingual sentence-transformer in this process, on CPU |
| `http` | an OpenAI-compatible `/embeddings` endpoint |
| `stub` | deterministic hashed n-grams, no weights |

> **Note on the default.** The spec asks for the locally loaded
> sentence-transformer "regardless of `AI_BACKEND`". That model needs weights
> on disk, and an offline CI runner has none — so `stub` keeps its promise of
> zero network by stubbing embeddings too, unless `AI_EMBED_BACKEND` says
> otherwise. Nothing else changes: for `local`, `domestic` and `external` the
> default is the real embedder, exactly as specified.

`sentence-transformers` (torch, ~2 GB) is therefore an optional install:

```bash
make install-embed                                   # locally
docker build --build-arg WITH_LOCAL_EMBEDDINGS=1 .   # bakes the weights in
```

## Caching

Mandatory, and keyed on `sha256(backend + model + prompt version + operation +
input)`. An in-process LRU sits in front of Redis db 8 with a 30-day TTL.
Duplicate seller titles are the norm — the same brake pad is listed by dozens
of shops, and `crawler` replays its whole archive when an algorithm changes —
so a repeated title must never reach the model twice.

Redis being down degrades the cache to the LRU and is logged; it never fails a
request. Embeddings are cached per text, and the "prompt version" slot of their
key is an embedding version of its own, so bumping a text prompt does not throw
away millions of perfectly good vectors.

## Budget

`AI_DAILY_BUDGET` is spent per UTC day. What it counts depends on what is
scarce: **wall-clock seconds** for a model on the host, **money** for a metered
provider (set `AI_COST_PER_1K_INPUT` / `AI_COST_PER_1K_OUTPUT` and it switches).
At 80% it logs a warning once; at 100% every call is refused with HTTP 429 and
`budget_exhausted`. A cached answer is still served — it costs nothing.

The counter lives in Redis so every container shares it. Without Redis it is
per process, which is a coarser guard but still a guard.

## Prompts

`src/ai/prompts/*.txt`, with `PROMPT_VERSION` in `src/ai/prompts.py`. The
version is part of every cache key, so **changing a prompt means bumping the
version** — a test enforces that by comparing a digest of the prompt files and
the registered schemas against `PROMPT_FINGERPRINT`:

```bash
make prompt-fingerprint   # after a deliberate change; then bump PROMPT_VERSION
```

## Configuration

Everything is an environment variable; nothing is a secret in code.

| Variable | Default | Meaning |
|---|---|---|
| `AI_BACKEND` | `stub` | `stub` / `local` / `domestic` / `external` |
| `AI_BASE_URL` | Ollama for `local` | OpenAI-compatible base URL of the **provider** |
| `AI_API_KEY` | — | bearer token, if the provider wants one |
| `AI_MODEL` | `qwen2.5:7b-instruct` | model name sent to the provider |
| `AI_JSON_MODE` | `true` | send `response_format: json_object` |
| `AI_TIMEOUT_SECONDS` | `60` | per-call timeout |
| `AI_MAX_ATTEMPTS` | `3` | total attempts, i.e. one call and two retries |
| `AI_MAX_CONCURRENCY` | `8` | in-flight model calls per process |
| `AI_EMBED_BACKEND` | see above | `local` / `http` / `stub` |
| `AI_EMBED_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | must be 384-dimension |
| `AI_EMBED_BATCH_SIZE` | `32` | internal batching |
| `REDIS_URL` | — | `AI_REDIS_URL` from `platform/.env` (db 8) |
| `AI_CACHE_TTL_SECONDS` | `2592000` | 30 days |
| `AI_DAILY_BUDGET` | `3600` | seconds, or currency when prices are set |
| `AI_BUDGET_ENABLED` | `true` | false runs unmetered |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `json` | structured logs to stdout |

> `platform/.env` already defines `AI_BASE_URL=http://ai:8000` — that is the
> address **other services** call this one at, not this service's provider. The
> compose file therefore feeds the container's `AI_BASE_URL` from
> `AI_BASE_URL_PROVIDER`, so the two never collide.

## Observability

| Metric | |
|---|---|
| `yadakchi_ai_calls_total{op,backend,status}` | requests served; `status=cached` is a cache hit |
| `yadakchi_ai_duration_seconds{op}` | end-to-end latency |
| `yadakchi_ai_cache_hits_total{op}` / `_cache_misses_total` / `_cache_hit_ratio` | the cache |
| `yadakchi_ai_model_invocations_total{op,backend}` | actual model calls — what the cache keeps down |
| `yadakchi_ai_tokens_total{direction}` | prompt / completion tokens |
| `yadakchi_ai_budget_used_ratio` | today's fill level, 1.0 means refusing |
| `yadakchi_ai_upstream_attempts_total{op,outcome}` | HTTP attempts including retries |

Titles are business data: above DEBUG the logs carry a length and a digest of a
prompt, never its content.

## Layout

```
src/ai/
├── main.py           FastAPI app, routes, error handlers, lifespan
├── config.py         settings, and the checks that fail at boot
├── service.py        cache → budget → call → validate → repair once
├── cache.py          LRU + Redis, and the cache key
├── budget.py         the daily guard behind 429 budget_exhausted
├── embeddings.py     stub / local / http providers, all 384-dimension
├── prompts.py        loading, PROMPT_VERSION, the fingerprint guard
├── text.py           Persian folding the stub needs
├── backends/         base.py, http.py (the one client), stub.py
├── schemas/          the registry; offer_fields.py
└── prompts/          system, extract_offer_fields, judge_same_part
```
