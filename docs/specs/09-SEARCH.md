# 09 — `search` service

**Build order: EIGHTH.** After `catalog`. Parallel with `billing`.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`, `02-EVENT-CONTRACTS.md`.
**You own:** `services/search/` and nothing else.

---

## What this service is

Given a Persian query and optionally a vehicle, return the right products in the right order.

Two distinct ranking problems exist in this system and must not be conflated: **which product** (yours) and **which seller within a product** (`catalog`'s). You rank products only — the seller ordering arrives pre-computed inside the product payload.

**Stack: Django 5 + Django Ninja + Typesense.** Django for query logs and synonym state; Ninja for the API.

---

## How it connects

| Direction | Peer | Channel |
|---|---|---|
| **consumes** | `catalog` | Kafka `yadakchi.products.changed.v1` *(compacted)* — the full renderable product |
| **consumes** | `fitment` | Kafka `yadakchi.vehicles.changed.v1`, `yadakchi.crossrefs.changed.v1` *(compacted)* |
| **consumes** | `ops` | Kafka `yadakchi.review.decided.v1` — approved synonyms |
| **serves** | `web` | HTTP search API |
| **calls** | `ai` | HTTP `POST /v1/embed` for query and document embeddings — vendor `services/ai/contracts/published/openapi.json` into your `contracts/consumed/ai-openapi.json` and generate from it. `ai` publishes it exactly so consumers do not guess the shape; never hand-write the request or response |
| owns | Typesense, Postgres `yadakchi_search`, Redis db 5 (from `SEARCH_REDIS_URL`) | |

**You never call `catalog`.** Everything needed to render a result is inside `products.changed`. That is why the payload is large.

### HTTP API (Django Ninja)

| Endpoint | Purpose |
|---|---|
| `GET /v1/search` | `q`, `vehicle_slug`, filters, page → ranked hits with enough data to render, plus facets |
| `GET /v1/suggest` | typeahead |
| `POST /v1/events/click` | `{query_id, product_uid, position}` — `web` reports which result was opened. This is the only way `clicked_position` reaches your query log: an outbound click goes to `billing`, which is a different event about a different thing (a seller click, not a result click), and nothing else observes the result list. Return the `query_id` from `GET /v1/search` so `web` can quote it back. Fire-and-forget: never let it fail a page |
| `GET /v1/health`, `GET /metrics` | |

Response carries a `fallback_applied: bool` flag (see below) and facet counts. Publish `openapi.json` to `contracts/published/`.

---

## Architectural decisions already made (do not revisit)

| Decision | Value |
|---|---|
| Engine | **Typesense** |
| Retrieval | **Hybrid lexical + vector** |
| Vehicle role | **Hard filter**, applied over the tri-state |
| Paid influence on ranking | **None. Ever** |

---

## The index

One Typesense collection, `products`. Fields:

`product_uid`, `title` (searchable), `title_variants[]` (member offer titles, deduped — improves recall on colloquial phrasing), `brand`, `part_type`, `part_type_synonyms[]`, `part_numbers[]` (including cross-referenced equivalents), `vehicle_compatible[]`, **`vehicle_incompatible[]`**, `authenticity_dominant`, `min_price_toman`, `offer_count`, `has_image`, `embedding` (float[384]), `updated_at`, plus the display fields needed to render a hit without a second call.

**Where the two derived fields come from.** Neither arrives ready-made, and both were previously unsourced:

- **`title_variants[]`** — the `title_normalized` of each member offer in `products.changed.offers[]`, deduped, minus the product `title` itself. It is what makes a product findable under a phrasing the representative offer never used. The field is optional on the wire: skip an offer that omits it rather than falling back to `raw_title`, which would put promotional junk into the index.
- **`part_type_synonyms[]`** — **yours, built locally**, not published by anyone. Keep a `part_type -> [token]` map fed by `review.decided` with `kind: synonym_candidate` and `decision: approve`, whose `subject` is `{token, part_type}`, and denormalize the matching tokens into each document at index time. Re-denormalize affected documents when a new approval arrives. The map starts empty and that is correct — at launch the field is `[]` on every document, and the vector channel is what carries colloquial phrasing until approvals accumulate. **A rejected or skipped candidate never enters it.**

**Store `vehicle_incompatible` explicitly.** The hard filter excludes only confirmed-incompatible. If you only store the compatible list and filter on membership, everything unmapped disappears — that is the exact compound risk this design exists to avoid.

## Persian query handling

Typesense's Persian analysis is weaker than Elasticsearch's. **Compensate upstream, not inside the engine.**

- Indexed text arrives already normalized from `catalog`. Never index raw titles.
- **Normalize the query with the same rules.** They are in `NORMALIZATION.md` in your own folder, ordered step by step, with `normalization-vectors.json` beside it — a table of input and expected output that your test suite must reproduce exactly. Copy the functions into your service; services do not share code. Index-side and query-side normalization must be symmetric — asymmetry is the classic silent search bug, and the vectors are what actually proves symmetry, since three agents can read the same prose and implement it three ways.
- Typo tolerance tuned for Persian word lengths.
- Synonyms: only **approved** ones, arriving via `review.decided` with `kind: synonym_candidate`, synced into the Typesense synonym API. **Candidate synonyms must never reach query expansion** — automatic extraction conflates synonyms with complements (brake pads and discs co-occur but are not the same thing). Candidates may inform ranking only.

## The part-number fast path — mandatory

Part numbers are the highest-intent queries in this vertical, and score fusion will bury an exact match under fuzzy hits.

If the normalized query matches the part-number shape — `^(?=.*[0-9])[A-Z0-9]{5,20}$` on the canonical form, defined with its canonicalization rules in `NORMALIZATION.md` — run an exact lookup against `part_numbers` first. For a query the whole string matching the shape is sufficient evidence and corroboration is **not** required: a user who types `425438` and nothing else has said what they mean. That is a deliberately lower bar than `enricher` applies when pulling a code out of a title, where surrounding words can mislead. Exact hits return at the top, ahead of hybrid results, never re-ranked below them. **Cover this with an explicit test** — it is easy to lose during a relevance-tuning pass.

## Hybrid retrieval

Lexical over `title`, `title_variants`, `brand`, `part_type_synonyms` with field weights; vector over `embedding` using the query embedding from `ai`; fuse with reciprocal rank fusion; then apply business signals (offer count, has image, price freshness, best seller trust).

Vector search is what connects colloquial part names to technical ones without an approved synonym for every pair. **Do not quietly drop it because lexical "seems fine".**

## Vehicle filtering

When `vehicle_slug` is set:

- **Exclude products where the slug is in `vehicle_incompatible`.** Do **not** require presence in `vehicle_compatible`.
- Confirmed-compatible products get a strong boost and a "fits your car" marker.
- Unknown-fitment products appear below, marked "compatibility unverified".

**Empty-result fallback — required, not optional.** If filtered results fall below a configured floor (default 5), re-run unfiltered and return those with `fallback_applied: true`. An empty page teaches the user we don't stock the part.

## Facets

Vehicle, brand, part type, authenticity, price range — first-class, with counts returned alongside results.

## Indexing

Consume `products.changed`, build the document, upsert into Typesense. Unpublished products are **removed**, not left stale. Bulk reindex must be resumable and rate-limited.

**The index is derived state and needs no backup** — a full rebuild replays the compacted `products.changed` topic from the beginning.

## Query logging — do not skip

Log every query: normalized text, vehicle, filters, result count, clicked position. `clicked_position` arrives via `POST /v1/events/click` and is therefore **best-effort** — a user who never clicks, or whose beacon is lost, leaves it null. Treat a null as "no click", never as position zero.

**Query logs are the second most valuable dataset in the project after the golden matching set.** They drive the catalog roadmap, the synonym dictionary, and part-type vocabulary expansion. Provide a **weekly zero-result report** — it is a product work queue, not a metrics curiosity.

---

## Project layout

```
services/search/
├── Dockerfile  requirements.txt  docker-compose.yml  Makefile  README.md
├── manage.py
├── contracts/
│   ├── consumed/{products.changed,vehicles.changed,crossrefs.changed,review.decided}.json
│   ├── consumed/ai-openapi.json      # vendored from services/ai/contracts/published/
│   └── published/openapi.json
├── src/search/
│   ├── settings.py  models.py  api.py
│   ├── text.py              # local copy of Persian normalization
│   ├── schema.py  indexer.py  query_builder.py  ranking.py  synonyms.py
│   ├── ai_client.py  query_log.py
│   └── management/commands/{consume_products,consume_reference,reindex_all,zero_result_report}.py
└── tests/
```

Compose: this service plus Typesense, Postgres, Redis, Kafka, and a stubbed `ai`.

---

## Acceptance criteria

1. A part-number query returns the exact-match product first, even when a fuzzy match scores higher.
2. Index-side and query-side normalization are proven symmetric across 30 Persian variants (ZWNJ, Arabic yeh/kaf, mixed digits).
3. A colloquial part name retrieves a product titled with the technical name via the vector channel, with no approved synonym present.
4. An unapproved candidate synonym does **not** affect query expansion.
5. With `vehicle_slug` set, confirmed-incompatible products are excluded and unknown-fitment products still appear, marked.
6. Below the result floor, the unfiltered fallback triggers and sets `fallback_applied`.
7. Facet counts are correct on a seeded dataset.
8. A full reindex by replaying `products.changed` from the beginning reproduces the incremental result set exactly.
9. Unpublishing a product removes it from the index within one consume cycle.
10. Zero-result queries are logged and the weekly report produces rows.
11. Consuming the same product event twice yields one document.
12. `mypy`, `ruff`, tests, `check-contracts` pass.

## Explicitly out of scope

Ranking sellers inside a product (`catalog`). Rendering results (`web`). Computing fitment (`fitment`) — you consume the tri-state, you do not derive it.

## Warnings

- **Never let payment influence ranking.** No boost tied to CPC, wallet balance, or panel membership. This is where neutrality would leak.
- **Never collapse tri-state fitment to boolean.**
- **Never index raw titles.**
