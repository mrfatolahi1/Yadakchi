# search

Owns the Typesense index and the search query API.

**Your instructions are in this folder.** [`BRIEF.md`](./BRIEF.md) is the shared
project brief — read it first, every time — and [`SPEC.md`](./SPEC.md) is the
full specification for this service, including its acceptance criteria. Together
they are everything you need: you own `services/search/` and nothing else, you
never read or modify another service's folder or database, and you talk to other
services only over Kafka (plus the few synchronous HTTP pairs the brief allows).
If something you need is missing from a spec, stop and report it rather than
inventing it.

Both files are copies, distributed from `docs/specs/` by `make sync-specs`.
**Do not edit them here** — CI compares them byte-for-byte against the source and
will fail the build. Spec changes happen in `docs/specs/`, reviewed by a human.

## Local development

Create a Python 3.12 virtual environment, install `requirements.txt`, then run
`make migrate` and `make check`. The following command includes the shared
platform infrastructure and starts only the search API, its two Kafka consumers,
and a deterministic stub of the synchronous AI embedding endpoint:

```sh
docker compose --env-file ../../platform/.env up --build
```

The API is available on `http://localhost:8090` by default.

The Typesense index is derived state. Start or resume a full compacted-topic
replay with `python manage.py reindex_all --new`; rerun without `--new` to resume
an interrupted consumer group.

Useful endpoints:

- `GET /v1/search`
- `GET /v1/suggest`
- `POST /v1/events/click`
- `GET /v1/health`
- `GET /metrics`
