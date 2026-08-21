# fitment

The fitment service owns Yadakchi's hand-written vehicle tree, tri-state part fitment,
and cross-brand part-number references. It consumes enriched offers and human review
decisions through Kafka; it has no synchronous dependency on another service.

## Local checks

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
make check
```

The default local database is SQLite. Runtime containers use the dedicated Postgres
database, Kafka, and Redis DB 3 supplied by the platform compose file.

## Runtime

```sh
make up
```

This combines `docker-compose.yml` with `../../platform/docker-compose.infra.yml` and
starts only fitment's web/admin process, two Kafka consumers, and transactional outbox
relay. The admin is available on port `8060`; Prometheus metrics are at `/metrics`.

Useful management commands:

- `seed_vehicles` idempotently loads the 29 model/trim rows and risky families.
- `emit_reference` republishes the complete compacted vehicle and cross-reference state.
- `recompute --apply-publication-gate` rebuilds fitments and applies the 70% launch gate.
- `publish_outbox --follow` relays durable outbound events to Kafka.

`BRIEF.md` and `SPEC.md` are distributed instruction copies and must not be edited here.
