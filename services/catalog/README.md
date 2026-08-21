# catalog

Owns canonical Products, sellers and price history, and serves the read API the website renders from.

**Your instructions are in this folder.** [`BRIEF.md`](./BRIEF.md) is the shared
project brief — read it first, every time — and [`SPEC.md`](./SPEC.md) is the
full specification for this service, including its acceptance criteria. Together
they are everything you need: you own `services/catalog/` and nothing else, you
never read or modify another service's folder or database, and you talk to other
services only over Kafka (plus the few synchronous HTTP pairs the brief allows).
If something you need is missing from a spec, stop and report it rather than
inventing it.

Both files are copies, distributed from `docs/specs/` by `make sync-specs`.
**Do not edit them here** — CI compares them byte-for-byte against the source and
will fail the build. Spec changes happen in `docs/specs/`, reviewed by a human.

---

## What it does

A match cluster arrives from `matcher` as a set of `offer_uid`s. This service
turns it into a page: it elects a representative to supply the title and image,
denormalises and ranks the seller list, computes price statistics and a price
history chart, aggregates fitment across the members, and decides whether the
result is substantial enough to publish. It also owns seller identity and the
trust score that drives the ranking.

Everything it decides goes out on `products.changed.v1` — the largest payload in
the system, deliberately, so `search` and `web` can render without ever calling
back.

## Running it

```bash
make up        # this service on the shared infrastructure
make logs
make down
```

`make up` starts the API on `127.0.0.1:8008`, one consumer process per topic,
and a Celery worker and beat. It needs the platform infrastructure, which its
own `docker-compose.yml` includes; no other service has to be running.

## Checks

```bash
make check     # lint, types, openapi freshness, contract drift, tests
make test      # just the tests, in a container
```

Tests run against a real Postgres — this service uses array columns with
`overlap`, JSONB and a range-partitioned table, none of which SQLite can stand
in for. `make test` brings up a throwaway database of its own, because the
platform's `catalog` role is `NOCREATEDB` and Django's test runner has to create
one.

## The parts worth knowing about

| Module | What it decides |
|---|---|
| `representative.py` | which member supplies the title and image, gated on the cluster's dominant authenticity claim |
| `titles.py` | the promotional-junk guard — the most expensive regression in this service |
| `ranking.py` | seller order: trust in bands first, then price, stock, freshness. Never CPC |
| `trust.py` | trust from price and stock accuracy we observed ourselves, with a capped cold start |
| `pricing.py` | in-stock-only statistics, and the downsampled daily chart |
| `rebuild.py` | assembles the whole product and applies the publication gate |
| `consumers/handlers.py` | one handler per topic, and both idempotency guards |
| `producer.py` | debounced, hash-suppressed emission |

## Three things that are easy to get wrong here

**The title comes from `title_normalized`, never `raw_title`.** Promotional
copy in a title reaches the HTML `<title>` tag and costs rankings across the
whole domain, not just one page. `titles.py` is a second lock on that door and
`tests/test_titles.py` holds a seeded junk-title set.

**`min_price_toman` counts in-stock offers only.** A headline price nobody can
buy is worse than no price. Out-of-stock offers stay on the page, labelled, and
simply do not vote.

**A product is never deleted.** It retires and points at its successor, and
every slug it has ever had keeps resolving. Splits are frequent with aggressive
merging upstream, and an unresolved old URL is a ranking that decays.

## Operational commands

```bash
python manage.py rebuild_all --emit      # recompute the catalogue after an algorithm change
python manage.py recompute_trust         # force a trust pass
python manage.py make_partitions         # provision price-history months ahead
python manage.py export_openapi          # refresh contracts/published/openapi.json
python manage.py consume_offers          # one long-running consumer per topic
```

Reprocessing is routine, not exceptional: `rebuild_all` recomputes every product
from the local read models without re-crawling or re-clustering anything, and a
consumer group reset replays a topic from the beginning.
