# 02 — Event Contracts (the wire format)

**Build order: SECOND.** After the platform, before any service.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`.
**You own:** the JSON Schema files in `services/*/contracts/published/`, and the shared envelope definition. **You write no service logic.**

---

## Why this document exists

Ten services built by different agents must agree on the wire format. There is **no shared code package** — each service is independent — so the agreement lives in JSON Schema files plus this document.

After this is merged, **payload shapes are frozen**. Changing one requires a version bump and human review.

Your job: write the schema files exactly as specified below, plus a validation test per schema, plus example payloads that service agents can use as fixtures.

---

## The envelope

Every Kafka message body is this envelope. `payload` differs per topic.

```json
{
  "event_id": "9f1c...",            // UUIDv4, unique per emission
  "event_type": "offers.enriched",  // topic name without prefix and version
  "version": 1,
  "occurred_at": "2026-08-20T09:14:22Z",
  "producer": "enricher",
  "trace_id": "abc123",             // propagated across the chain for debugging
  "payload": { }
}
```

The Kafka **message key** is specified per topic below and must be set — compaction and partition ordering depend on it.

**Rules:**
- Unknown fields in a received envelope are ignored, never rejected. This lets producers add optional fields without breaking consumers.
- Removing a field, renaming a field, or changing a type is a **breaking change**: publish to `.v2` and run both until consumers migrate.
- `trace_id` originates in `crawler` and is carried through the whole chain. Every service logs it.
- A **tombstone** is a message with `payload: null` on a compacted topic, meaning "this entity is deleted".

## Shared enumerations

Repeated verbatim in every schema that uses them.

```
authenticity_claim : genuine | oem | aftermarket | used | refurbished | unknown
fitment_status     : compatible | incompatible | unknown
stock_status       : in_stock | out_of_stock | unknown
provenance         : rule | model | human | catalog | consensus
seller_tier        : new | standard | trusted | suspended
```

## Shared identity rules

```
offer_uid    = sha256("{source_key}:{external_key}")[:32]   minted by enricher
cluster_uid  = UUIDv4                                        minted by matcher
product_uid  = the same value as cluster_uid                 adopted by catalog
vehicle_slug = kebab-case, e.g. peugeot-206-type-5           minted by fitment
seller_key   / source_key = stable slugs                     minted by crawler
```

Money is always `*_toman`, integer. Timestamps are ISO-8601 UTC with `Z`.

---

## Topic payloads

### `yadakchi.listings.observed.v1`
**crawler → enricher.** Key: `{source_key}:{external_key}`

```
source_key        string
external_key      string        seller's own id or url path — stable within the source
url               string
raw_title         string
raw_price_text    string|null   untouched, e.g. "۲٬۴۵۰٬۰۰۰ تومان"
raw_stock_text    string|null
image_url         string|null
raw_fragment      string        HTML/JSON of this listing only, capped at 64 KB
archive_uri       string        MinIO path to the full page snapshot
fragment_hash     string        sha256 of raw_fragment
observed_at       timestamp
```

The full page lives in MinIO, not in Kafka. The fragment is inline because `enricher` needs it and re-fetching would be expensive.

**`fragment_hash` hashes the fragment, not the page.** `crawler` keeps a second hash, `page_hash`, over the full fetched page bytes; that one names the archive object and drives archive deduplication, and it **stays inside `crawler`** — it is not on the wire. The two values are different, and conflating them silently breaks change detection: a page whose surrounding markup changed but whose listing did not must not look like a changed listing. Downstream, `fragment_hash` is the only hash that means "this listing changed".

### `yadakchi.offers.enriched.v1`
**enricher → fitment, matcher.** Key: `offer_uid`

```
offer_uid              string
source_key             string
external_key           string
seller_key             string
url                    string
raw_title              string
title_normalized       string
brand                  string|null
part_number            string|null      normalized, uppercase, no separators
part_type              string|null      controlled vocabulary key
authenticity_claim     enum
pack_quantity          integer
price_toman            integer|null
stock_status           enum
image_url              string|null
vehicle_hints          string[]         raw, unresolved
overbroad_claim        boolean          "fits all Peugeots" was present
confidences            object           field name → 0..1
extraction_provenance  object           field name → provenance
normalizer_version     string
first_seen_at          timestamp
last_seen_at           timestamp
is_active              boolean
```

### `yadakchi.offers.fitted.v1`
**fitment → matcher, catalog.** Key: `offer_uid`

```
offer_uid      string
fitments       [ { vehicle_slug, status: enum, confidence, provenance, evidence: object } ]
crossref_codes string[]                 equivalent part numbers found for this offer
risky_family   { part_type, required_granularity, note_fa } | null
computed_at    timestamp
```

### `yadakchi.vehicles.changed.v1`  *(compacted)*
**fitment → matcher, catalog, search, web.** Key: `vehicle_slug`

```
vehicle_slug     string
brand            string
model            string
trim             string|null
year_from        integer|null
year_to          integer|null
engine_code      string|null
display_name_fa  string
aliases          string[]
is_published     boolean
updated_at       timestamp
```

### `yadakchi.crossrefs.changed.v1`  *(compacted)*
**fitment → catalog, search.** Key: `{code_a}|{code_b}` with `code_a < code_b`

```
code_a, code_b     string
brand_a, brand_b   string|null
confidence         number
provenance         enum
updated_at         timestamp
```

### `yadakchi.clusters.changed.v1`
**matcher → catalog.** Key: `cluster_uid`

```
cluster_uid            string
members                [ { offer_uid, confidence, provenance } ]
change_reason          string     created | member_added | member_removed | split | merged
predecessor_uids       string[]   clusters this one absorbed or was split from
successor_uid          string|null  set when this cluster is retired
computed_at            timestamp
```

### `yadakchi.products.changed.v1`  *(compacted)*
**catalog → search, ops, web.** Key: `product_uid`

The full renderable product. This is the largest payload in the system and it is deliberate: `search` and `web` must never call back to `catalog` to render a result.

```
product_uid             string
slug                    string
title                   string
brand                   string|null
part_type               string|null
authenticity_dominant   enum
image_url               string|null
part_numbers            string[]
crossref_codes          string[]
vehicles_compatible     string[]     vehicle_slugs
vehicles_incompatible   string[]
vehicles_unknown        string[]
risky_family_note_fa    string|null
offer_count             integer
min_price_toman         integer|null   in-stock offers only
max_price_toman         integer|null   in-stock offers only
median_price_toman      integer|null
offers                  [ { offer_uid, seller_key, seller_name, price_toman,
                            stock_status, authenticity_claim, trust_score,
                            rank_position, url, is_cheapest } ]
price_series            [ { date, min_toman, median_toman } ]   downsampled
is_published            boolean
successor_product_uid   string|null    set on split; drives 301 redirects
updated_at              timestamp
```

### `yadakchi.sellers.changed.v1`  *(compacted)*
**catalog → billing, ops.** Key: `seller_key`

```
seller_key      string
name            string
domain          string
source_key      string|null
is_panel        boolean
tier            enum
trust_score     number
price_accuracy  number|null
stock_accuracy  number|null
updated_at      timestamp
```

### `yadakchi.clicks.recorded.v1`
**billing → catalog, matcher.** Key: `product_uid`

```
click_id       string
product_uid    string
offer_uid      string
seller_key     string
cost_toman     integer
is_suspicious  boolean
occurred_at    timestamp
```

Consumers use this only for **traffic-derived priority** (review queue ordering, crawl tiering). It is not financial truth — that stays inside `billing`.

### `yadakchi.review.requested.v1`
**matcher, crawler, fitment → ops.** Key: `request_uid`

```
request_uid   string
kind          string    merge_pair | split_product | adapter_broken | synonym_candidate | price_ambiguous
priority      integer   higher is more urgent, traffic-derived where applicable
subject       object    kind-specific identifiers
evidence      object    everything a human needs to decide, inline
requested_at  timestamp
```

`evidence` must be **self-sufficient**. `ops` must never need to query another service to render the review screen.

### `yadakchi.review.decided.v1`  *(compacted, infinite retention)*
**ops → matcher, fitment.** Key: `request_uid`

```
request_uid  string
kind         string
decision     string    same_product | different_products | approve | reject | skip
subject      object
actor        string
reason       string|null
decided_at   timestamp
```

**This topic is never deleted.** Human decisions are sticky: after any full reprocess, consumers replay this topic last and human decisions override everything computed.

---

## Consumer obligations

Every consumer must:

1. **Be idempotent.** Key handling on the event's natural identity, not on arrival order.
2. **Commit offsets only after durable write.** At-least-once plus idempotency.
3. **Tolerate out-of-order delivery across partitions.** Use `occurred_at` and per-entity versioning; never assume global ordering.
4. **Dead-letter after bounded retries**, to the topic's `.dlq` companion, with the original envelope and the traceback.
5. **Bootstrap from the beginning on compacted topics** when its local read model is empty.

---

## Acceptance criteria

1. A JSON Schema file exists at `services/<producer>/contracts/published/<topic>.json` for all eleven topics, and validates the example payloads.
2. A `consumed/` copy exists in every consumer, byte-identical, and `make check-contracts` passes.
3. At least three realistic example payloads per topic exist under `contracts/examples/`, using **real Persian spare-parts data** — service agents will use them as test fixtures.
4. A test proves an envelope with an unknown extra field validates successfully.
5. A test proves a tombstone (`payload: null`) validates on compacted topics and is rejected on non-compacted ones.
6. A test proves `offer_uid` derivation is stable and matches the documented formula.
7. Every schema declares `additionalProperties: true` at the payload level and `required` for non-nullable fields.

## Explicitly out of scope

Database tables — each service designs its own. Service logic. HTTP API schemas, which each service publishes as its own OpenAPI document.

## If something is missing

If a service later needs a field that isn't here, the correct action is: **stop, and raise a contract change request.** Adding a field inside one service is how two services end up disagreeing.
