# 05 — `enricher` service

**Build order: FOURTH.** After `ai` and `crawler`.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`, `02-EVENT-CONTRACTS.md`.
**You own:** `services/enricher/` and nothing else.

---

## What this service is

Turns a raw observed listing into a **structured Offer**: clean Persian title, brand, part number, part type, authenticity claim, pack quantity, price in **integer tomans**, stock status, and vehicle hints — each with a confidence and provenance.

Everything downstream depends on this. A field you fail to extract is a match candidate `matcher` never generates.

**Stack: Django 5.** No HTTP API. One long-running Kafka consumer as a management command. Django gives you models, migrations, admin for inspecting extraction results, and management commands for reprocessing.

---

## How it connects

| Direction | Peer | Channel |
|---|---|---|
| **consumes** | `crawler` | Kafka `yadakchi.listings.observed.v1` |
| **produces** | `fitment`, `matcher` | Kafka `yadakchi.offers.enriched.v1` |
| **produces** | `ops` | Kafka `yadakchi.review.requested.v1` — `kind: price_ambiguous \| synonym_candidate` |
| **calls** | `ai` | HTTP `POST /v1/extract` — cascade stage two only |
| owns | Postgres `yadakchi_enricher`, Redis db 2 | |

**On `review.requested.v1`:** you produce to this topic but do not own its schema — `matcher` does. Hold a byte-identical `consumed/` copy; never place this file in `published/`. `make sync-contracts` puts it there for you, and `make check-contracts` fails the build if two services publish the same topic.

### Event you consume — `yadakchi.listings.observed.v1`

```
source_key, external_key, url, raw_title, raw_price_text, raw_stock_text,
image_url, raw_fragment, archive_uri, content_hash, observed_at
```

### Event you produce — `yadakchi.offers.enriched.v1`

Key: `offer_uid`. You **mint** it: `sha256("{source_key}:{external_key}")[:32]`. Carry the incoming `trace_id` through.

```
offer_uid, source_key, external_key, seller_key, url,
raw_title, title_normalized,
brand, part_number, part_type, authenticity_claim, pack_quantity,
price_toman, stock_status, image_url,
vehicle_hints[], overbroad_claim,
confidences{}, extraction_provenance{}, normalizer_version,
first_seen_at, last_seen_at, is_active
```

Emit **only when something material changed** (any extracted field differs from the last emission for that `offer_uid`) or when the normalizer version changed. A pure re-observation with identical values emits nothing.

---

## Economic model — read before designing

This runs **once per new Offer**, not once per crawl. A price changes ten times a day; a brand is extracted once. Compute here is affordable — but only if you never reprocess unchanged titles.

**The cascade:**

1. **Rules first (free).** Target **70–80%** of records fully resolved deterministically.
2. **Model second (paid).** Only records where rules left a required field missing or low-confidence go to `ai`.
3. **Never call the model on every record.** If you are, the design is wrong.

If `ai` returns **429 `budget_exhausted`**, fall back to rules-only output with reduced confidence, mark the offer for later reprocessing, and continue. **Never fail the pipeline over model unavailability.**

---

## Part one: Persian normalization

The foundation everything stands on. Pure functions in `text.py`.

| Transform | Detail |
|---|---|
| Character unification | Arabic `ي`→`ی`, `ك`→`ک`; Arabic-Indic and Persian digits → Latin |
| Diacritics | strip harakat |
| ZWNJ | normalize نیم‌فاصله: collapse repeats, drop meaningless, preserve semantic |
| Whitespace | collapse runs, trim, normalize non-breaking spaces |
| Punctuation | unify Persian/Latin comma, question mark, parentheses |
| Symbols | strip emoji and decorative characters |
| Promotional tokens | remove a maintained stoplist: free shipping, guarantee claims, "اورجینال ۱۰۰٪", star runs, phone numbers, Telegram/WhatsApp handles, seller name suffixes |

**Keep both forms.** `raw_title` untouched; `title_normalized` cleaned. The normalized form becomes page titles and search text — **promotional junk must never reach the SEO surface.**

Expose `normalize_text`, `normalize_part_number`, and `strip_promotional` separately.

## Part two: Field extraction

**Brand** — gazetteer with Persian and Latin spellings and common misspellings (عظام/Ezam, کروز/Crouse, والئو/Valeo, بوش/Bosch, ایساکو/ISACO…). Word-boundary aware. Two matches lowers confidence and escalates to the model. Store the canonical key, not the surface form.

**Part number** — regex family for Iranian OEM formats (long numeric, ISACO-style) and international alphanumerics. Validate shape and length before accepting; a stray 6-digit number is usually not a part number. Absence is normal — do not force it.

**Part type** — a **controlled vocabulary** YAML mapping canonical keys (`brake_pad_front`) to Persian surface forms including trade synonyms (`لنت جلو`, `لنت ترمز جلو`, `بالشتک جلو`). Seed ~150 highest-volume types across the four vehicles. Longest match wins; record which surface form matched.

**Authenticity claim** — keyword mapping: `اصلی`/`اورجینال`→`genuine`, `شرکتی`→`oem`, `طرح`/`متفرقه`→`aftermarket`, `استوک`→`used`, `تعمیری`→`refurbished`, else `unknown`. **This is the seller's claim and is never verified.** Store it in its own field, not merely inside the title — a later policy change must not require reprocessing. Do not infer truth from price.

**Pack quantity** — detect explicit counts (`دست چهار عددی`, `بسته ۴ تایی`, `جفت`). Default 1, with lower confidence for types usually sold in sets.

**Price — the highest-risk field.** Rial vs toman ambiguity is the single most dangerous thing in this project; a currency error makes the cheapest result on a page wrong by 10x.

- Explicit unit words in the source take precedence.
- Otherwise apply a **plausibility band per `part_type`** from config: if the value sits in the rial-plausible band and is 10x above the toman band, treat as rials and divide.
- Plausible under neither interpretation → `price_toman = null`, low confidence, and emit `review.requested` with `kind: price_ambiguous`. **Never guess.**
- Dedicated regression suite with **30+ real examples**.

**Stock status** — map source phrases to the enum. Absence is `unknown`, never `in_stock`.

**Vehicle hints** — extract raw vehicle-looking strings into an array **without resolving them**; resolution is `fitment`'s job. Set `overbroad_claim: true` when patterns like `مناسب تمام پژوها` or `همه مدل‌ها` appear — `fitment` rejects these and needs to know they were present.

## Part three: Synonym mining

The part-type vocabulary is the backbone of extraction, and it will always be incomplete — Iranian sellers invent trade names faster than anyone maintains a YAML file. You are the only service that sees every title next to the type it resolved to, so candidate synonyms are mined here and nowhere else.

Two signals, both cheap, both by-products of work you already do:

- **Surface forms that matched.** Part-type matching records *which* surface form hit (`لنت جلو` → `brake_pad_front`). A form that keeps resolving through a long-match fallback, or an alias appearing far more often than the canonical form, is evidence the vocabulary should name it directly.
- **Unmapped tokens that co-occur with a known type.** Track title tokens that appear repeatedly alongside an already-resolved `part_type` and belong to no vocabulary entry. A token that co-occurs with one part type well above a configured threshold, across **several distinct sellers**, is a candidate for that type.

Emit each candidate as `review.requested` with `kind: synonym_candidate`. Require a minimum count and more than one seller before emitting; one seller's idiosyncratic spelling is noise, and a review queue full of noise stops being read. Deduplicate on the candidate token plus part type so a re-run does not enqueue the same pair twice.

The `evidence` object must carry everything a reviewer needs **inline**: the candidate token or surface form, the part type it is proposed for, the co-occurrence count, the number of distinct sellers, and a handful of example titles. `ops` renders the review screen from the event alone and must never call back to you for context.

You do not consume the decision. Approved synonyms flow from `ops` to `search`, which is the service that applies them; a candidate that is approved and also belongs in the extraction vocabulary is a human edit to your YAML, not an automatic one. **Never expand your own matching on an unapproved candidate** — automatic mining conflates synonyms with complements (brake pads and discs co-occur constantly and are not the same thing).

## Confidence and provenance

Every field carries a confidence in `[0,1]` and a provenance (`rule` or `model`). Derive confidence from something real — match specificity, competing matches, model certainty. **Fabricated confidence is worse than none.**

## Reprocessing

Every Offer stores `normalizer_version`. A management command re-runs extraction for stale offers **from your own stored copy of the observed listing**, never from the network. Resumable, rate-limited so it cannot starve live traffic, and it must not reset `first_seen_at`.

For a full system rebuild, `crawler` replays `listings.observed` and you simply consume it again — idempotently.

---

## Django models

`Offer` (all extracted fields, `offer_uid` unique, `normalizer_version`, `first_seen_at`, `last_seen_at`, `is_active`), `ObservationRecord` (raw fragment copy, content_hash, observed_at — your local input archive), `ProcessedEvent` (event_id, processed_at — the idempotency guard).

Register `Offer` in admin with search and filters. Being able to eyeball extraction results is worth the ten minutes.

---

## Project layout

```
services/enricher/
├── Dockerfile  requirements.txt  docker-compose.yml  Makefile  README.md
├── manage.py
├── contracts/
│   ├── consumed/{yadakchi.listings.observed.v1,yadakchi.review.requested.v1}.json
│   └── published/yadakchi.offers.enriched.v1.json
├── src/enricher/
│   ├── settings.py  models.py  admin.py
│   ├── text.py
│   ├── extractors/{brand,part_number,part_type,authenticity,price,quantity,vehicle_hints}.py
│   ├── cascade.py  ai_client.py  producer.py
│   ├── vocab/{brands,part_types,promo_stoplist,price_bands}.yaml
│   └── management/commands/{consume_listings,reprocess}.py
└── tests/{test_text,test_price_currency}.py
```

Your compose brings up this service plus Postgres, Redis, Kafka, and a **stubbed `ai`** (or `AI_BACKEND=stub` pointed at a local fake). No other service.

---

## Acceptance criteria

1. `normalize_text` passes a table-driven test of 40+ Persian edge cases covering every transform.
2. Brand coverage above **90%** on a 200-row sample of real crawled titles.
3. Part type coverage above **90%** on the same sample.
4. **Zero currency errors** on the 30-case price suite; ambiguous cases yield `null` plus a review event, never a guess.
5. On 1,000 records, **at least 70%** resolve by rules alone — proven by counting `ai` calls.
6. The full pipeline runs offline against a stubbed `ai`.
7. A 429 from `ai` produces rules-only output and a reprocess flag, not a failure.
8. Consuming the same `listings.observed` event twice produces one Offer and **one** emitted event.
9. An unchanged re-observation emits **nothing**.
10. A normalizer version bump plus `reprocess` rewrites all offers with zero network calls to seller sites.
11. Emitted events validate against the published schema; `make check-contracts` passes.
12. `mypy`, `ruff`, tests pass.

## Explicitly out of scope

Resolving vehicle hints to vehicles (`fitment`). Deciding whether two offers are the same product (`matcher`). **Verifying authenticity claims** — we store the claim, we never check it; this is a deliberate product decision. Anything about products.

## Warnings

- Do not let promotional text reach `title_normalized` — it ends up in page titles.
- Do not default missing stock to `in_stock`.
- Do not infer authenticity from price.
