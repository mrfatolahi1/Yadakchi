# 08 — `catalog` service

**Build order: SEVENTH.** After `matcher`.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`, `02-EVENT-CONTRACTS.md`.
**You own:** `services/catalog/` and nothing else.

---

## What this service is

Turns a match cluster into a **presentable product**: title, image, attributes, ranked seller list, price statistics, price history. Also owns seller identity and trust scoring.

This is where SEO and user trust are actually manufactured.

**Stack: Django 5 + Django Ninja + Celery.** Django Ninja because `web` and `ops` call you synchronously. Celery for scheduled recomputation.

---

## How it connects

| Direction | Peer | Channel |
|---|---|---|
| **consumes** | `matcher` | Kafka `yadakchi.clusters.changed.v1` |
| **consumes** | `enricher` | Kafka `yadakchi.offers.enriched.v1` |
| **consumes** | `fitment` | Kafka `yadakchi.offers.fitted.v1`, `yadakchi.vehicles.changed.v1`, `yadakchi.crossrefs.changed.v1` |
| **consumes** | `billing` | Kafka `yadakchi.clicks.recorded.v1` |
| **produces** | `search`, `ops`, `web` | Kafka `yadakchi.products.changed.v1` *(compacted)* |
| **produces** | `billing`, `ops` | Kafka `yadakchi.sellers.changed.v1` *(compacted)* |
| **serves** | `web`, `ops` | HTTP read API |
| owns | Postgres `yadakchi_catalog`, Redis db 5 | |

### Produced — `products.changed.v1` (compacted, key: `product_uid`)

**The largest payload in the system, deliberately.** It carries the entire renderable product so `search` and `web` never call back. Full field list is in spec 02; it includes the ordered offer list, all three vehicle status arrays, cross-reference codes, the downsampled price series, `is_published`, and `successor_product_uid`.

`product_uid` **is** the `cluster_uid` from `matcher`, adopted unchanged.

### HTTP API (Django Ninja)

| Endpoint | Purpose |
|---|---|
| `GET /v1/products/{slug}` | full product view for `web`; returns 301 target if retired |
| `GET /v1/products/by-uid/{product_uid}` | same, by identity |
| `POST /v1/products/batch` | many products by uid, for search result hydration fallback |
| `GET /v1/sellers/{seller_key}` | seller profile for `ops` |
| `GET /v1/health`, `GET /metrics` | |

Publish `openapi.json` to `contracts/published/`.

---

## Architectural decisions already made (do not revisit)

| Decision | Value |
|---|---|
| Canonical representative | **Best Offer** — one member supplies title and image |
| Identity | **Stable UUID + mutable slug**, plus a successor pointer |
| Out of stock | **Retained with a label**, not deleted |
| Seller sort | **Trust score first**, not cheapest first |

---

## Part one: Representative selection

Score each member on field completeness (high weight), seller trust (medium), image present and unwatermarked (medium), title length in a sane band (low).

Hard rules:

- **Use `title_normalized`, never `raw_title`.** Promotional junk goes straight into the HTML title tag and damages SEO. This is the most common regression here.
- **Tie the representative to the cluster's dominant authenticity claim.** With aggressive merging the top-scoring member may be an aftermarket copy while most of the cluster is genuine. Compute `authenticity_dominant` as the modal claim, then prefer a matching representative.
- **Record the choice with provenance** so re-election is deterministic when the representative disappears. The product must never have an empty title.

## Part two: Identity and URLs

`product_uid` is assigned once and never changes. `slug` is Persian, URL-safe, derived from title plus a short identity suffix, and **may change** when the title improves.

`GET /v1/products/{slug}` must resolve old slugs and retired products to a redirect target using `successor_product_uid`. With aggressive merging, splits are frequent; without this, dead URLs accumulate and rankings decay.

## Part three: The ranked offer list

Rebuild whenever a cluster or any member offer changes. For each active member denormalize price, stock, authenticity claim, seller, trust score, url, and a computed `rank_position`.

**Ranking = trust score first**, then price, then stock, then price freshness. Weighted formula, weights in config.

Two mandatory display rules:

1. **`min_price_toman` is computed from in-stock offers only.** The headline lowest price must never be a number nobody can buy. Out-of-stock offers still appear, labeled.
2. **Mark exactly one offer `is_cheapest: true`** so the frontend can badge it prominently even when trust-first ordering puts another seller first. Users came for price; hiding the cheapest feels like bait-and-switch.

## Part four: Trust score

Sellers have no history on day one, so trust-first ordering is initially meaningless. Compute a proxy from:

| Signal | Source |
|---|---|
| **Price accuracy** — did the crawled price match on recrawl | your own observation of `offers.enriched` history |
| **Stock accuracy** — was "in stock" true on recrawl | same |
| Domain age, contact completeness, trust badge | carried in offer data |
| Panel membership | `is_panel` |

**Price and stock accuracy matter most** because we own them and sellers cannot fake them. Stale prices and fictional stock are penalized immediately.

New sellers start at `tier: new` with a capped trust ceiling and a "new seller" badge — **visibility is earned.** This is the only quality gate in the system, since onboarding is open self-serve and authenticity is unverified.

Recompute on a Celery schedule; emit `sellers.changed` on any change.

## Part five: Price history

You observe price changes through `offers.enriched`. Append to a **monthly-partitioned** `PriceHistory` table on actual change only, never per event. Compute a downsampled daily series (min and median across in-stock offers) and include it in `products.changed` — `web` must not need a second call to draw the chart.

## Part six: Publication gate

`is_published` is true only when there is at least one active offer, a non-empty title, and — for risky part families — an explicit fitment verdict or the attached warning.

**Every published page must have real content.** Since the indexing policy is "index everything", a thin page is a domain-wide SEO liability. Even a single-offer product must carry fitment, cross-reference equivalents, price history, and related parts.

---

## Django models

`Product`, `ProductOffer`, `Seller`, `PriceHistory` (partitioned), `OfferReadModel`, `FitmentReadModel`, `VehicleReadModel`, `CrossRefReadModel`, `ClickCounter`, `ProcessedEvent`.

---

## Project layout

```
services/catalog/
├── Dockerfile  requirements.txt  docker-compose.yml  Makefile  README.md
├── manage.py
├── contracts/
│   ├── consumed/{clusters.changed,offers.enriched,offers.fitted,vehicles.changed,crossrefs.changed,clicks.recorded}.json
│   └── published/{products.changed,sellers.changed,openapi}.json
├── src/catalog/
│   ├── settings.py  models.py  admin.py  api.py
│   ├── representative.py  ranking.py  trust.py  slugs.py  pricing.py  related.py
│   ├── producer.py  tasks.py
│   └── management/commands/{consume_*,rebuild_all,recompute_trust,make_partitions}.py
└── tests/
```

Compose: this service plus Postgres, Redis, Kafka.

---

## Acceptance criteria

1. `GET /v1/products/{slug}` for a 20-offer product executes a small bounded number of queries — asserted with a query counter, not by eye.
2. Product titles never contain promotional tokens or phone numbers, against a seeded junk-title set.
3. Removing the representative offer triggers deterministic re-election; the title is never empty.
4. `min_price_toman` ignores out-of-stock offers while those offers still appear labeled.
5. A mostly-genuine cluster does not pick an aftermarket representative.
6. A cluster split sets `successor_product_uid`; the old slug returns a redirect target.
7. Trust is computed from observed price and stock accuracy; new sellers rank below established ones at equal price.
8. Exactly one offer per product carries `is_cheapest`.
9. One `products.changed` event per material change, debounced — five changes in a minute emit once.
10. Replaying all input topics from the beginning into an empty database reproduces identical products.
11. A thin product fails the publication gate.
12. `mypy`, `ruff`, tests, `check-contracts` pass.

## Explicitly out of scope

Deciding which offers belong together (`matcher`). Rendering HTML (`web`). Search indexing (`search`). Charging for clicks (`billing`).

## Warnings

- **Never sort by CPC.** Paid placement is rejected; neutrality is the core asset.
- **Never delete a product.** Retire it and point the successor.
- **Never make the product payload vary by user or vehicle.** It must be globally cacheable; vehicle-specific messaging is a client-side concern in `web`.
