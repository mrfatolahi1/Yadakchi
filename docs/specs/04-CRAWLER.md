# 04 — `crawler` service

**Build order: THIRD.** After platform and contracts. Parallel with `ai`.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`, `02-EVENT-CONTRACTS.md`.
**You own:** `services/crawler/` and nothing else.

---

## What this service is

Fetches listings from Iranian online spare-parts sellers, archives the raw bytes, and emits one event per observed listing.

**This service interprets nothing.** It does not parse prices into numbers, identify brands, or clean Persian text. It fetches, archives, and announces. Everything downstream is rebuildable from what you archive — if you lose or mangle raw data, the system is unrecoverable without re-crawling.

**Stack: Django 5 + httpx/asyncio + Celery.** Django for the source registry, archive metadata, migrations, and admin. Celery for tiered scheduling. Kafka consumption: none.

> Scrapy was considered and rejected: its Twisted reactor conflicts with the Django + Kafka process model, and we implement tiered scheduling and politeness ourselves anyway.

---

## How it connects

| Direction | Peer | Channel | Detail |
|---|---|---|---|
| **produces** | `enricher` | Kafka `yadakchi.listings.observed.v1` | one message per observed listing, key `{source_key}:{external_key}` |
| **produces** | `ops` | Kafka `yadakchi.review.requested.v1` | `kind: adapter_broken` when parse rate collapses |
| **consumes** | `billing` | Kafka `yadakchi.clicks.recorded.v1` | traffic signal for crawl tiering — maintain a small local read model of clicks per `product_uid`; tolerate its absence early on |
| writes | MinIO bucket `raw-archive` | S3 API | **exclusive owner** |
| writes | Postgres `yadakchi_crawler` | — | **exclusive owner** |

### Event you produce — `yadakchi.listings.observed.v1`

Envelope per spec 02, `producer: "crawler"`. You **mint `trace_id`** here; it flows through the entire chain.

```
source_key      string     e.g. "yadakmarket"
external_key    string     seller's own id or url path, stable within the source
url             string
raw_title       string     untouched
raw_price_text  string|null   untouched, e.g. "۲٬۴۵۰٬۰۰۰ تومان"
raw_stock_text  string|null
image_url       string|null
raw_fragment    string     HTML/JSON of this listing only, capped at 64 KB
archive_uri     string     MinIO path to the full page snapshot
content_hash    string     sha256 of the fragment
observed_at     timestamp
```

The full page goes to MinIO; only the per-listing fragment goes on the wire. `enricher` needs the fragment and re-fetching would be expensive.

---

## Architectural decisions already made (do not revisit)

| Decision | Value |
|---|---|
| Sourcing | Hybrid — crawl to bootstrap the catalog, convert sellers to a panel later |
| Extraction | **Hand-written adapter per source.** No generic LLM page extraction |
| Scope | Parts fitting Peugeot 206, Pride/Tiba, Samand/Pars, Peugeot 405 |
| Source ceiling | **15–20 sources.** Hand-written adapters mean maintenance scales linearly |
| Politeness | Respect `robots.txt` and per-source rate limits |
| Feeds | If a source offers an XML/CSV product feed, use it. Crawl only sources without one |

---

## Components

### Adapter framework

```python
class Adapter(Protocol):
    key: str
    def discover(self, source) -> Iterator[str]: ...
    def extract_listings(self, raw: bytes, url: str) -> list[ListingStub]: ...
```

Two base classes: `HtmlAdapter` (CSS/XPath selectors declared as class attributes, so a new source is ~30 lines) and `FeedAdapter` (XML/CSV product feeds; the Google Merchant shape is common in Iran). Registry keyed by `Source.adapter_key`.

### Fetcher

`httpx` with HTTP/2 and pooling. Per-source delay enforced by a **Redis token bucket** so parallel workers share one budget. Honest user agent with a contact URL. Optional proxy. `robots.txt` fetched and cached daily; **disallowed URLs are skipped and counted, never fetched.**

### Archive

Full page gzipped to MinIO at `{source_key}/{yyyy}/{mm}/{dd}/{content_hash}.gz`.

**Deduplication:** if the newest archive record for `(source_id, url_hash)` has the same `content_hash`, do not write a new object — record the observation referencing the existing one. Most refetches are unchanged; without this, storage is unbounded.

Retention: prune objects older than 180 days only when superseded. Configurable.

### Tiered scheduler

| Tier | Selection | Frequency |
|---|---|---|
| Hot | listings on products with the most clicks in 7 days | hourly |
| Warm | active listings with recent price changes | 6-hourly |
| Cold | everything else active | daily |
| Discovery | category and listing pages | daily |
| Dormant | inactive 30+ days | weekly, then dropped |

Celery beat dispatches per source per tier. **Runs must be resumable** — persist a cursor so a restart does not begin at page one.

### Adapter health — the most important operational feature here

After each run write a health row: attempted, parsed_ok, parse_rate. Maintain a rolling 7-day baseline per source.

**If parse rate falls more than 20% below baseline, emit `review.requested` with `kind: adapter_broken`** and increment `yadakchi_adapter_health_alerts_total{source}`.

Hand-written adapters break silently when a site changes markup. Without this alarm you find out weeks later, after the catalog has rotted.

### Replay — required

A management command `replay_archive --source= --since= --until=` re-emits `listings.observed` for every archived document, **without touching seller sites**. This is the system-wide rebuild path: when `enricher` changes its normalizer, the whole pipeline is regenerated from here.

Replay must be resumable, rate-limited, and must reuse the original `observed_at` and `content_hash`.

---

## Django models (your database only)

`Source` (key, name, base_url, kind, adapter_key, priority, politeness_delay_ms, is_active), `ArchivedDocument` (source, url, url_hash, http_status, fetched_at, archive_uri, content_hash, error), `Observation` (source, external_key, url_hash, content_hash, observed_at, emitted_at), `AdapterHealth` (source, window, attempted, parsed_ok, parse_rate, baseline_rate, alerted), `CrawlCursor` (source, tier, position, updated_at), `ClickSignal` (product_uid, count_7d, updated_at — local read model).

Register everything in Django admin — the source registry is edited by humans.

---

## Project layout

```
services/crawler/
├── Dockerfile  requirements.txt  docker-compose.yml  Makefile  README.md
├── manage.py
├── contracts/
│   ├── published/yadakchi.listings.observed.v1.json
│   ├── published/yadakchi.review.requested.v1.json
│   └── consumed/yadakchi.clicks.recorded.v1.json
├── src/crawler/
│   ├── settings.py  models.py  admin.py
│   ├── fetcher.py  archive.py  robots.py  scheduler.py  health.py
│   ├── producer.py            # Kafka producer + envelope
│   ├── consumers/clicks.py    # management command
│   ├── adapters/{__init__,base,<source_key>}.py
│   └── management/commands/{crawl,replay_archive,consume_clicks}.py
└── tests/fixtures/            # saved real pages, gzipped
```

Your `docker-compose.yml` brings up this service plus Postgres, Redis, Kafka, and MinIO from `platform/docker-compose.infra.yml`. No other service.

**Fixtures are mandatory.** Every adapter ships with a saved real page and a test asserting the expected stubs. This is how breakage is caught in CI rather than in production.

---

## Deliverables

Seed `Source` with real Iranian spare-parts sellers — a mix of marketplaces and single shops, **no more than 20**. Ship **at least three working adapters** covering the four target vehicles, plus `adapters/README.md` explaining how to add a fourth in under an hour.

---

## Acceptance criteria

1. A crawl run produces archive objects in MinIO whose sha256 matches the recorded `content_hash`.
2. Re-running with unchanged upstream content writes **no new archive objects**.
3. A `robots.txt`-disallowed URL is never fetched; a counter records the skip.
4. Two concurrent workers on one source respect the combined politeness delay — timing test against a local server.
5. At least three adapters pass fixture-based extraction tests.
6. Dropping a source's parse rate below 80% of baseline emits `review.requested` with `kind: adapter_broken`.
7. An interrupted crawl resumes from its cursor.
8. Exactly one `listings.observed` message is produced per newly observed listing, validating against the published schema.
9. `replay_archive` re-emits historical events with zero outbound requests to seller sites.
10. The clicks consumer is idempotent — replaying the topic does not double-count.
11. `mypy`, `ruff`, tests pass.

## Explicitly out of scope

Any interpretation of content: no price parsing, no brand detection, no Persian normalization, no fitment. No product concept. No trust scores.

## Warnings

- **Do not use an LLM to parse pages.** Hand-written adapters is a settled decision, rejected on cost grounds.
- **Do not exceed 20 sources** without flagging it — past that, the maintenance model breaks.
- **Do not crawl aggressively.** Reputational damage and IP blocking cost more than the extra pages are worth.
