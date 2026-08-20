# yadakchi

A vertical price-comparison engine for **automotive spare parts in Iran**. Users search for a
part; we show the same part across many sellers, side by side, with two answers no generic
comparison site gives: **does it fit your car**, and **is it genuine, OEM, aftermarket, used or
refurbished**.

This repository is one Git repo containing **ten fully independent services**. Each has its own
dependencies, its own database, its own Dockerfile and its own tests. They talk to each other
over Kafka, never by reaching into each other's data.

> **New agent working on a service?** Everything you need is in your own folder:
> `services/<name>/BRIEF.md`, `services/<name>/SPEC.md`, `services/<name>/README.md`.
> You do not need to read this file or any other spec.

---

## Get infrastructure running (about five minutes)

You need **Docker** with the Compose plugin, **Python 3.12**, `make`, and roughly 40 GB of free
RAM if you intend to run everything at the declared limits.

```bash
git clone <this repo> && cd yadakchi

make env         # creates platform/.env from platform/.env.example
                 # ---> open it and change every password before you leave your laptop

make infra-up    # postgres, kafka, redis, minio, typesense, prometheus, grafana
                 # waits until every container reports healthy

make topics      # applies platform/kafka/topics.yml to the broker (idempotent)
```

That is it. Check it:

```bash
make ps                    # containers and their status
make topics-list           # the 17 topics (11 declared + 6 dead-letter companions)
make psql svc=matcher c='select extname from pg_extension'
```

| What | Where | Credentials |
|---|---|---|
| Kafka UI | <http://localhost:8080> | none (loopback only) |
| Grafana | <http://localhost:3001> | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` |
| Prometheus | <http://localhost:9090> | none |
| MinIO console | <http://localhost:9001> | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| Postgres | `localhost:15432` | one user per service, see `platform/.env` |
| Kafka (from the host) | `localhost:19092` | — |
| Typesense | `localhost:8108` | `TYPESENSE_API_KEY` |

To run the whole system, services included:

```bash
make up          # infrastructure + every service that has a Dockerfile yet
make down        # stop; volumes are kept
make clean       # stop and DELETE all data (asks first)
```

`make up` starts only the services whose agents have delivered a `Dockerfile`, so the system is
runnable throughout the build-out.

---

## Layout

```
yadakchi/
├── Makefile                     every command a human needs; `make help`
├── docker-compose.yml           the whole system, for a human
├── platform/                    the shared substrate — owned by nobody, used by everyone
│   ├── docker-compose.infra.yml infra only; each service's compose file includes this
│   ├── .env.example             every credential in the system enters here
│   ├── postgres/init.sql        eight databases, eight users, no way across
│   ├── kafka/topics.yml         the topic registry: partitions, retention, who writes, who reads
│   ├── minio/init.sh            the raw-archive bucket and crawler's key
│   ├── observability/           prometheus, alert rules, provisioned grafana
│   ├── scripts/                 the drift guards and the spec distributor
│   └── tests/                   platform tests, including the acceptance criteria
├── docs/specs/                  SOURCE OF TRUTH for all specs
└── services/                    ten independent projects
    ├── ai/        crawler/   enricher/  fitment/  matcher/
    └── catalog/   search/    billing/   ops/      web/
```

## How the ten services fit together

```
crawler ──listings.observed──▶ enricher ──offers.enriched──▶ fitment ──offers.fitted──▶ matcher
                                                                │                          │
                                                     vehicles/crossrefs.changed      clusters.changed
                                                                │                          │
                                                                ▼                          ▼
                                    search ◀──products.changed── catalog ◀─────────────────┘
                                       │                          │
                                       └────────▶ web ◀───────────┘        billing ──clicks.recorded──▶
                                                                            ops ──review.decided──▶
```

Three rules make this work, and they are absolute:

1. **Forward data flow is asynchronous, over Kafka.** Never a synchronous call.
2. **Events carry full payloads**, not identifiers — a consumer never calls back to ask what
   happened. That is what makes bulk reprocessing possible.
3. **No service reads another service's database.** Postgres enforces it: each service has its own
   database and its own user, and `CONNECT` is revoked from everyone else. `make verify` proves
   all 56 forbidden pairs are actually refused.

Synchronous HTTP exists only for: `enricher`/`matcher`/`search` → `ai`, `web` → `catalog`,
`web` → `search`, and `ops` → `catalog`/`billing`.

## The two guards

Ten agents write ten services and never read each other's code. Two checks stand between that
and a repository that quietly forks. Both block CI.

**Contract drift.** Every event schema is owned by exactly one service, in
`services/<owner>/contracts/published/<topic>.json`, and copied byte-for-byte into every service
that reads it, in `contracts/consumed/`. `platform/kafka/topics.yml` declares who is who.

```bash
make check-contracts    # fails, naming the service and topic, if a copy drifted
make sync-contracts     # the one way to update copies after a deliberate change
```

Editing a `consumed/` copy fails the build. Change the schema in the owning service, run
`make sync-contracts`, commit both.

**Spec drift.** `docs/specs/` is the source of truth. `make sync-specs` copies the project brief
and each service's own spec into its folder as `BRIEF.md` and `SPEC.md`, and those copies are
committed — so a fresh clone hands each agent its complete instructions with no setup step.

```bash
make sync-specs         # distribute (idempotent)
make check-specs        # fails if a distributed copy was edited directly
```

## Everyday commands

| Command | What it does |
|---|---|
| `make help` | list every target |
| `make infra-up` / `make infra-down` | the platform only |
| `make up` / `make down` | everything |
| `make topics` | apply the topic registry (safe to re-run) |
| `make psql svc=<name>` | a shell in that service's database |
| `make check-contracts` / `make sync-contracts` | event schema drift |
| `make check-specs` / `make sync-specs` | spec distribution |
| `make lint` / `make typecheck` / `make test` | platform quality gates |
| `make verify` | acceptance tests against the running infrastructure |
| `make ci` | everything CI gates on, locally |

## CI

GitHub Actions, path-filtered: a change under `services/matcher/` runs the matcher job and
nothing else; a change to `platform/`, the root files or `docs/specs/` runs everything.
`platform/scripts/ci_matrix.py` makes that decision and is unit-tested, so the filtering is
verifiable without pushing a branch.

The contract guard, the spec guard and the compose render always run. `.github/CODEOWNERS` puts
`docs/specs/`, `platform/`, `.github/` and every `contracts/published/**` behind human review —
event schema changes are not something an agent merges on its own.

## Conventions that apply everywhere

- Python 3.12, fully typed, `ruff` and `mypy` clean. `pip` + `requirements.txt`.
- Django 5 where there is a database, Django Ninja where there is an HTTP API. Never DRF.
- **Money is integer tomans.** Never floats, never rials.
- **Timestamps are timezone-aware UTC** in storage; Tehran time only at display.
- Structured JSON logs to stdout. Never `print()`.
- **Kafka consumers are idempotent**, and offsets are committed only after a durable write.
  Replaying history must produce the same state — reprocessing is routine here, not exceptional.
- No secrets in code. Environment variables only.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `network yadakchi not found` | `make net` (or just use `make infra-up`, which creates it) |
| Postgres restarts on first boot | `platform/.env` is missing a `*_DB_PASSWORD`; `init.sql` fails loudly by design |
| `make topics` cannot reach the broker | wait for health: `docker inspect --format '{{.State.Health.Status}}' yadakchi-kafka` |
| A password change did not take effect | database credentials are created once, on an empty volume. `make clean` re-initialises everything |
| Ports already in use | every host port is configurable in `platform/.env` |
| A service container is missing from `make up` | it has no `Dockerfile` yet — that service has not been built |
