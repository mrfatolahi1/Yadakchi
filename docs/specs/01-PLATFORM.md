# 01 — Platform (shared infrastructure)

**Build order: FIRST.** Nothing else can start until this is merged.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`.
**You own:** the repository root, `platform/`, the root `docker-compose.yml`, the root `Makefile`, and CI. **You own no service.**

---

## Goal

Stand up the shared substrate every service depends on, plus the monorepo skeleton, plus CI. When you are done, a service agent can `cd services/<name>` and have a working, isolated environment.

You write **no business logic** and create **no service code**. If you find yourself writing a crawler or a model, stop.

---

## Repository skeleton

```
yadakchi/
├── README.md
├── Makefile
├── docker-compose.yml               # whole system, for the human
├── .gitignore  .dockerignore  .editorconfig
├── .github/
│   ├── CODEOWNERS
│   └── workflows/ci.yml
├── platform/
│   ├── docker-compose.infra.yml     # infra only — services include this
│   ├── .env.example
│   ├── postgres/init.sql
│   ├── kafka/topics.yml
│   ├── kafka/create_topics.sh
│   ├── minio/init.sh
│   ├── observability/
│   │   ├── prometheus.yml
│   │   └── grafana/
│   └── scripts/
│       ├── check_contracts.py       # contract drift guard
│       └── wait_for.py
├── docs/specs/                      # source of truth for all fourteen specs
└── services/
    ├── ai/  crawler/  enricher/  fitment/  matcher/
    ├── catalog/  search/  billing/  ops/  web/
```

Create each `services/<name>/` with a `README.md` naming the owning spec, plus the distributed spec copies described below. Service agents fill in the rest.

---

## Spec distribution — every service folder is self-sufficient

Each service is built by a different agent, working **only inside its own folder**. That agent must find its instructions there, without navigating the repository or reading twelve specs that don't concern it.

`docs/specs/` is the source of truth. You distribute copies:

```
services/search/
├── BRIEF.md      ← copy of docs/specs/00-PROJECT-BRIEF.md
├── SPEC.md       ← copy of docs/specs/09-SEARCH.md
└── README.md     ← points at both, in one paragraph
```

Mapping:

| Service folder | `SPEC.md` source |
|---|---|
| `ai` | `03-AI.md` |
| `crawler` | `04-CRAWLER.md` |
| `enricher` | `05-ENRICHER.md` |
| `fitment` | `06-FITMENT.md` |
| `matcher` | `07-MATCHER.md` |
| `catalog` | `08-CATALOG.md` |
| `search` | `09-SEARCH.md` |
| `billing` | `10-BILLING.md` |
| `ops` | `11-OPS.md` |
| `web` | `12-WEB.md` |

Requirements:

- **`make sync-specs`** copies `00-PROJECT-BRIEF.md` and the mapped spec into every service folder. Idempotent.
- **The copies are committed to Git**, not gitignored. A fresh checkout must give an agent everything it needs inside its own folder with no setup step.
- **CI blocks on drift.** A job verifies every `services/*/BRIEF.md` and `services/*/SPEC.md` is byte-identical to its source in `docs/specs/`. Editing a copy directly must fail the build — the source of truth is `docs/specs/`, and `make sync-specs` is the only way to update copies.
- **`.github/CODEOWNERS` assigns `docs/specs/` to the repository owner.** Spec changes are human-reviewed.
- Each `services/<name>/README.md` states, in one short paragraph: what the service does, that `BRIEF.md` and `SPEC.md` are the full instructions, and that the agent must not work outside this folder.

This mirrors the `make sync-contracts` mechanism exactly: one source of truth, distributed copies, and a CI guard against silent divergence.

---

## PostgreSQL — one server, isolated databases

Single Postgres 16 container using the `pgvector/pgvector:pg16` image.

`platform/postgres/init.sql` must create **eight databases and eight users**, each user owning only its own database:

| Database | User |
|---|---|
| `yadakchi_crawler` | `crawler` |
| `yadakchi_enricher` | `enricher` |
| `yadakchi_fitment` | `fitment` |
| `yadakchi_matcher` | `matcher` |
| `yadakchi_catalog` | `catalog` |
| `yadakchi_search` | `search` |
| `yadakchi_billing` | `billing` |
| `yadakchi_ops` | `ops` |

Rules:
- `REVOKE CONNECT ON DATABASE ... FROM PUBLIC` for every database, then grant only to its own user.
- Enable `vector` in `yadakchi_matcher`, and `pg_trgm` in `yadakchi_matcher` and `yadakchi_search`.
- **No FDW, no dblink, no shared schema.** Isolation is enforced by the engine, not by convention. This is deliberate: agents cannot violate the boundary even by accident.
- Each service runs its own Alembic/Django migrations against its own database. You create none of their tables.

Tune `shared_buffers`, `work_mem`, and `max_connections` for a 64–128 GB host with eight client services.

## Kafka

Single-broker KRaft-mode Kafka (no ZooKeeper). Acceptable for one server; document that production hardening means three brokers.

`platform/kafka/topics.yml` declares every topic, its partition count, retention, and cleanup policy. `create_topics.sh` applies it idempotently on startup.

| Topic | Partitions | Cleanup | Retention |
|---|---|---|---|
| `yadakchi.listings.observed.v1` | 6 | delete | 90 days |
| `yadakchi.offers.enriched.v1` | 6 | delete | 90 days |
| `yadakchi.offers.fitted.v1` | 6 | delete | 90 days |
| `yadakchi.vehicles.changed.v1` | 1 | **compact** | — |
| `yadakchi.crossrefs.changed.v1` | 3 | **compact** | — |
| `yadakchi.clusters.changed.v1` | 6 | delete | 90 days |
| `yadakchi.products.changed.v1` | 6 | **compact** | — |
| `yadakchi.sellers.changed.v1` | 1 | **compact** | — |
| `yadakchi.clicks.recorded.v1` | 3 | delete | 30 days |
| `yadakchi.review.requested.v1` | 3 | delete | 30 days |
| `yadakchi.review.decided.v1` | 1 | **compact** | **infinite** |

Compacted topics carry reference state: a new consumer reads from the beginning and rebuilds its local read model. `review.decided` is never deleted because human decisions are permanent.

Also create a `.dlq` companion topic for every non-compacted topic.

Provide a UI (Kafka UI or Redpanda Console) on the internal network for debugging.

## Redis

One container, but **databases 0–9 allocated one per service**, documented in `platform/.env.example`. Redis is for cache and locks only — never for inter-service events.

## MinIO

One container, one bucket `raw-archive`, owned exclusively by `crawler`. Others have no credentials for it.

## Typesense

One container, owned exclusively by `search`.

## Observability

Prometheus scraping every service's `/metrics`, Grafana with provisioned datasource and a starter dashboard, Sentry DSN passed through environment. Alerting rules can be stubbed.

---

## `platform/docker-compose.infra.yml`

This is the file every service's own compose file will `include` or `extends`. It must:

- Bring up postgres, kafka, redis, minio, typesense, prometheus, grafana with healthchecks and named volumes.
- Attach everything to an external network named `yadakchi`.
- Set `mem_limit` on every container. On a 64 GB host, suggested: postgres 24g, kafka 8g, typesense 6g, redis 4g, minio 2g, observability 2g.
- Be runnable standalone: `make infra-up`.

## Root `docker-compose.yml`

Brings up infra **plus all ten services**, for the human running the whole system. Service agents do not use this file — they use their own.

---

## Root `Makefile`

| Target | Behaviour |
|---|---|
| `infra-up` / `infra-down` | platform only |
| `up` / `down` | everything |
| `topics` | apply `kafka/topics.yml` |
| `psql svc=<name>` | shell into that service's database |
| `check-contracts` | run the contract drift guard |
| `sync-specs` | distribute `docs/specs/` into service folders |
| `check-specs` | fail if any distributed copy has drifted |
| `ci` | what CI runs, locally |

---

## Contract drift guard — the most important thing you build

Every service publishes JSON Schema files for the events it produces, at `services/<name>/contracts/published/<topic>.json`. Every consumer keeps a copy at `services/<consumer>/contracts/consumed/<topic>.json`.

`platform/scripts/check_contracts.py` must:

1. Verify every `consumed/` file is byte-identical to the corresponding `published/` file.
2. Verify every topic in `kafka/topics.yml` has exactly one publisher.
3. Verify no service consumes a topic it has no `consumed/` copy of.
4. Exit non-zero on any mismatch, printing exactly which service and topic drifted.

Wire it into CI as a **blocking** job. With different AI agents working in different folders, this check is the only thing standing between you and two services disagreeing about a field name.

Also provide `make sync-contracts` which copies publisher schemas into consumer folders, so resolving drift is one command after a deliberate change.

## CI

GitHub Actions, **path-filtered**: a change under `services/matcher/` runs only the matcher job.

Per-service Python job: `ruff check`, `ruff format --check`, `mypy`, `pytest`.
The `web` job: `tsc --noEmit`, `eslint`, `next build`, unit tests.
Always-run jobs: `check-contracts`, and a build of the root compose file.

`.github/CODEOWNERS` assigns `platform/`, `.github/`, and every `services/*/contracts/published/**` to you. **Event schema changes require human review.**

---

## Acceptance criteria

1. `make infra-up` brings every infrastructure container to healthy.
2. Eight databases exist; connecting as `enricher` to `yadakchi_matcher` is **refused**. Prove this with a test.
3. `make topics` creates every topic with the declared partitions, cleanup policy, and retention; running it twice is a no-op.
4. Producing to a compacted topic twice with the same key and reading from the beginning yields one record.
5. `pgvector` is available in `yadakchi_matcher`; `pg_trgm` in `matcher` and `search`.
6. MinIO bucket exists and is reachable only with the crawler credentials.
7. `check-contracts` passes on the empty skeleton, and fails when a `consumed/` copy is edited by one byte.
8. CI path filtering demonstrably skips unrelated service jobs.
9. `docker stats` shows memory limits on every container.
10. A new engineer can follow the root `README.md` and get infra running in under ten minutes.
11. `make sync-specs` places `BRIEF.md` and the correct `SPEC.md` in all ten service folders; running it twice changes nothing.
12. Editing `services/search/SPEC.md` directly makes `make check-specs` and CI fail, naming the drifted file.
13. On a fresh clone with no setup, `services/search/` already contains `BRIEF.md`, `SPEC.md`, and a `README.md` pointing at both.

## Explicitly out of scope

Any service code, any table, any topic payload definition (that is spec 02), any business logic.
