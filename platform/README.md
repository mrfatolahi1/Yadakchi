# platform/

The shared substrate: Postgres, Kafka, Redis, MinIO, Typesense and observability, plus the
guards that keep ten independently built services agreeing with each other.

**If you are a service agent, you do not own anything in this folder.** You connect to what is
here; you never run, modify or extend it. Your instructions are in `services/<your-service>/`.

| Path | What it is |
|---|---|
| `docker-compose.infra.yml` | every infrastructure container, with healthchecks, named volumes and memory limits. Your service's own compose file `include`s this. |
| `.env.example` | every credential and endpoint in the system. Copy to `.env`; `.env` is git-ignored. |
| `postgres/init.sql` | eight databases and eight users, created once on an empty volume. `CONNECT` is revoked from everyone but the owner, so cross-service reads are impossible rather than merely discouraged. |
| `postgres/postgresql.conf` | tuning for a 24 GB container serving eight services. |
| `kafka/topics.yml` | the topic registry: partitions, cleanup policy, retention, message key, and who produces and consumes each topic. The source of truth for both the broker and the contract guard. |
| `kafka/create_topics.sh` | applies the registry to the broker. Idempotent; runs on every startup. |
| `minio/init.sh` | the `raw-archive` bucket and crawler's scoped, delete-less credentials. |
| `observability/` | Prometheus scrape config and alert rules, provisioned Grafana datasource and starter dashboard. |
| `scripts/` | the contract drift guard, the contract sync, the spec distributor and its guard, the CI path filter, and `wait_for.py`. |
| `tests/` | the platform's own tests, including every acceptance criterion in spec 01. `pytest -m infra` runs the ones that need containers. |

## Two things worth knowing before you change anything here

**Database locale.** The eight databases are created with `C.UTF-8`, not `C`. Under the `C`
locale `pg_trgm` extracts no trigrams at all from Persian text — `similarity()` returns 0 — which
would quietly break fuzzy matching in `matcher` and `search`. There is a test for it.

**Compaction and retention are declared, not incidental.** Compacted topics are the ones a new
consumer replays from the beginning to rebuild a local read model, and
`yadakchi.review.decided.v1` is never deleted because human decisions are permanent. Changing a
cleanup policy in `topics.yml` changes what a downstream service can rebuild.
