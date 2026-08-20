# 00 — Project Brief (yadakchi)

**Give this file to every AI agent, always, alongside its service spec.** It is the shared context, and it is deliberately short.

---

## What we are building

`yadakchi` is a vertical price-comparison engine for **automotive spare parts in Iran**. Users search for a part; we show the same part across many online sellers with prices side by side.

Two things differentiate it from generic price comparison engines, and both are core:

1. **Fitment** — does this part actually fit *your* car (brand / model / trim)?
2. **Authenticity** — is this genuine, OEM, aftermarket, used, or refurbished? Prices differ 4x across these, so comparing them without labels is misleading.

**Phase one covers four high-volume Iranian vehicles:** Peugeot 206, Pride/Tiba, Samand/Pars, Peugeot 405.

**Business model:** free for users; sellers pay **per outbound click (CPC)**. Ranking is never purchasable — neutrality is the core asset.

---

## Architecture: ten independent services

One Git repository, ten **fully separate projects** under `services/`. Each has its own dependencies, its own database, its own Dockerfile, its own tests, and its own local dev environment.

**You own exactly one service directory. You never read or modify another.** Everything you need is inside your own folder and your own spec.

| Service | Stack | Responsibility |
|---|---|---|
| `ai` | **FastAPI** (no database) | field extraction, pair adjudication, embeddings |
| `crawler` | Django + httpx + Celery | fetch listings from seller sites, raw archive |
| `enricher` | Django | Persian normalization, field extraction |
| `fitment` | Django + admin | vehicle tree, part↔vehicle mapping, cross-references |
| `matcher` | Django + numpy + pgvector | decide which listings are the same product |
| `catalog` | Django + Django Ninja | canonical products, sellers, price history, read API |
| `search` | Django + Django Ninja + Typesense | search index and query API |
| `billing` | Django + Django Ninja | outbound redirect, CPC accounting, wallets |
| `ops` | Django + admin + HTMX | internal console, review queue, seller dashboard |
| `web` | **Next.js / TypeScript** | public website |

Django everywhere there is a database, because ORM + migrations + management commands + admin are exactly what this architecture needs repeatedly. `ai` is FastAPI because it has no database and heavy ML dependencies. `web` is Next.js for ISR and SEO.

### Communication rules — absolute

- **Forward data flow is asynchronous, over Kafka.** Never a synchronous call.
- **Events carry full payloads, not just identifiers.** A consumer must never call back to the producer to learn what happened. This is what makes services independent and bulk reprocessing possible.
- **Each service keeps its own local read models** of foreign data, built from events.
- **Synchronous HTTP is allowed only for these pairs:**

| From | To | Purpose |
|---|---|---|
| `enricher`, `matcher`, `search` | `ai` | extraction, adjudication, embeddings |
| `web` | `catalog` | product pages |
| `web` | `search` | search results |
| `ops` | `catalog`, `billing` | seller dashboard |

- **No service ever reads another service's database.** Each has its own database and credentials; Postgres enforces this.
- **Kafka is between services. Celery is inside a service** for scheduled or parallel work. A Kafka consumer is a long-running Django management command, not a Celery task. Only `crawler`, `matcher`, and `catalog` need Celery at all.

### Cross-service identifiers

Because databases are separate, **integer primary keys never cross a service boundary.** Use these stable string identities in every event and API:

| Identity | Format | Minted by |
|---|---|---|
| `offer_uid` | first 32 hex chars of `sha256("{source_key}:{external_key}")` | `enricher` |
| `cluster_uid` / `product_uid` | UUIDv4 — the same value; `matcher` mints it, `catalog` adopts it | `matcher` |
| `vehicle_slug` | e.g. `peugeot-206-type-5` | `fitment` |
| `seller_key` | e.g. `yadakyar` | `crawler` |
| `source_key` | e.g. `yadakmarket` | `crawler` |

### Reprocessing is a first-class requirement

When an algorithm changes, we reprocess everything. That is routine, not exceptional. `crawler` can replay its entire raw archive into Kafka; every downstream service rebuilds from the event log. **Design every consumer so replaying history produces correct state.**

---

## Non-negotiable principles

1. **SEO is the primary acquisition channel.** Pages must be server-rendered with real content in the HTML.
2. **Raw data is never discarded.** Everything must be rebuildable without re-crawling seller sites.
3. **Every automated decision is reversible and carries provenance** (rule, model, or human).
4. **Human decisions are sticky.** A full reprocess must never erase them.
5. **Entity resolution is the heart of the product**, not the UI.

## The three-tier data model

| Tier | Meaning | Owned by |
|---|---|---|
| **Raw** | Immutable snapshot of a fetched page | `crawler` |
| **Offer** | One seller's listing: price, stock, link | `enricher` |
| **Product** | Canonical product many Offers map onto. **We construct it; it does not exist in source data** | `catalog` |

---

## Shared infrastructure

Provided by the platform (spec 01). You connect to it; you never run or modify it.

| Component | Purpose |
|---|---|
| **Kafka** | the event backbone; long retention; replayable |
| **PostgreSQL 16** | one server, **one separate database and user per service** |
| **Redis** | per-service cache and locks. **Not an event bus** |
| **Typesense** | search index (owned by `search`) |
| **MinIO** | raw archive object storage (owned by `crawler`) |
| Prometheus + Grafana, Sentry | metrics and errors |

---

## Universal conventions

**Python services:**

- **Python 3.12**, fully type-annotated, `mypy` must pass.
- **`pip` + `requirements.txt`.** No Poetry, no uv.
- **`ruff format` and `ruff check`** must pass.
- **Django 5.x** with **Django Ninja** where an HTTP API is needed. Never DRF.
- **Pydantic v2** for all event and API schemas.
- **Kafka consumers must be idempotent.** Replaying a message must never duplicate data or corrupt state. This is the most common failure in AI-generated code in this system — write the guard explicitly and test it.
- **Consumer offsets are committed only after the work is durably written.** At-least-once delivery plus idempotency, never at-most-once.
- **Structured JSON logging** to stdout. Never `print()`.
- **No secrets in code.** Environment variables only.
- **Money is integer tomans.** Never floats, never rials.
- **Timestamps are timezone-aware UTC** in storage; Tehran time only at display.

**`web` (TypeScript):** Next.js App Router, TypeScript strict, ESLint + Prettier, no state library beyond React built-ins unless justified.

---

## What you must never do

- Never read or write another service's database.
- Never call another service synchronously except the allowed pairs above.
- Never publish an event not declared in your spec, and never change a payload shape without a version bump.
- Never call an LLM provider SDK directly — go through the `ai` service.
- Never invent a field in an event you consume. If something you need is missing, **stop and report it** rather than adding it silently.
- Never use an integer primary key as a cross-service identifier.

## Glossary

| Term | Meaning |
|---|---|
| **Offer** | One seller's listing of a part |
| **Product** | Canonical part that Offers cluster into |
| **Fitment** | Which vehicles a part fits |
| **Cross-reference** | Equivalent part numbers across brands |
| **Blocking** | Cheaply narrowing match candidates before expensive comparison |
| **Golden dataset** | Human-labeled Offer pairs used to measure matching precision |
| **Trim** (تیپ) | Vehicle variant, e.g. "206 Type 5" — different trims need different parts |
| **Authenticity claim** | Seller's stated grade: genuine / OEM / aftermarket / used / refurbished |
