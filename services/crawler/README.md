# yadakchi crawler

The crawler fetches seller bytes, archives complete immutable page snapshots in MinIO, and publishes
one `yadakchi.listings.observed.v2` event when a listing's raw fragment changes. It does not parse
prices, normalize Persian, infer brands/authenticity, or decide vehicle fitment.

## Processes

- Django/admin owns the source registry, archive metadata, cursors, health history, click signals,
  and durable Kafka outbox.
- Celery beat dispatches hot, warm, cold, discovery, and dormant crawl tiers; Celery workers fetch
  pages with shared Redis politeness reservations.
- `consume_clicks` maintains the local seven-day `offer_uid` click read model. It never calls billing
  or catalog.
- `replay_archive` publishes the exact stored observation payload without contacting seller sites.

## Local development

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
make migrate
make check
```

The complete service and shared platform infrastructure start with:

```bash
docker compose up --build
```

Compose includes `../../platform/docker-compose.infra.yml` and reads its environment from
`../../platform/.env`. It starts only shared infrastructure plus crawler web/admin, migration,
Celery worker/beat, and click-consumer processes. Run the isolated container check with
`make compose-check`.

Useful commands:

```bash
make crawl SOURCE=isacostore TIER=discovery
make replay SOURCE=isacostore ARGS='--since=2026-08-01T00:00:00Z --rate=25 --reset'
make consume-clicks
```

## Safety and idempotency

- `ArchiveService.archive` hashes the original response bytes before gzip. An unchanged
  `(source, URL)` reuses the existing object and metadata row.
- `observe_listing` locks the listing history, compares `fragment_hash`, and creates the Observation
  and outbox row in one database transaction. A repeated crawl creates neither another observation
  nor another event.
- `flush_outbox` validates every message against its local JSON Schema and marks it sent only after
  Kafka acknowledges delivery. Kafka producer idempotence is enabled.
- `consume_click_event` writes a unique `ConsumedClick(click_id)` receipt and updates `ClickSignal`
  transactionally. `ClickConsumerRunner.run_once` commits the Kafka offset only after that function
  returns from its committed transaction.

The full replay payload is stored on `Observation` deliberately. Re-extracting old pages with a
newly changed adapter would not reproduce the event originally observed.

## Adapters and health

Three hand-written adapters ship with saved, permitted real pages:

- ISACO Store: listing-page cards for Peugeot 206, with discovery routes for 405/Pars/Samand.
- YadakYar: product metadata for Pride and Tiba.
- Saray Yadak: product metadata for 206, 405/Pars, and Samand.

See `src/crawler/adapters/README.md` before adding a source. Every completed run records attempted
pages and pages producing at least one stub. A parse rate below 80% of the preceding seven-day
baseline creates one self-contained `adapter_broken` review request per source/day and increments
`yadakchi_adapter_health_alerts_total{source}`.

## Retention note

`ARCHIVE_RETENTION_DAYS` is exposed for policy visibility, but this service does not automatically
delete raw objects. The service brief says raw data is never discarded and replay must remain
possible; automatic 180-day pruning would violate that invariant. A reviewed project-wide retention
decision is required before a destructive command is added.
