# 06 — `fitment` service

**Build order: FIFTH.** After `enricher`.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`, `02-EVENT-CONTRACTS.md`.
**You own:** `services/fitment/` and nothing else.

---

## What this service is

Answers **"does this part fit this car?"** and maintains the table of equivalent part numbers across brands.

This is the product's competitive moat. Generic price comparison engines don't have it, which is exactly why they are useless for spare parts.

**Stack: Django 5 with heavy use of Django admin.** The vehicle tree, risky part families, and cross-reference pairs are human-edited reference data — the admin is a genuine win here, not boilerplate.

---

## How it connects

| Direction | Peer | Channel |
|---|---|---|
| **consumes** | `enricher` | Kafka `yadakchi.offers.enriched.v1` |
| **consumes** | `ops` | Kafka `yadakchi.review.decided.v1` — human fitment corrections |
| **produces** | `matcher`, `catalog` | Kafka `yadakchi.offers.fitted.v1` |
| **produces** | `matcher`, `catalog`, `search`, `web` | Kafka `yadakchi.vehicles.changed.v1` *(compacted)* |
| **produces** | `catalog`, `search` | Kafka `yadakchi.crossrefs.changed.v1` *(compacted)* |
| owns | Postgres `yadakchi_fitment`, Redis db 3 | |

### Consumed — `offers.enriched.v1`
You need: `offer_uid`, `part_number`, `part_type`, `brand`, `vehicle_hints[]`, `overbroad_claim`, `title_normalized`. Keep a **local read model of offers** built from this topic — you need it for consensus across sellers.

### Produced — `offers.fitted.v1` (key: `offer_uid`)
```
offer_uid, fitments[{vehicle_slug, status, confidence, provenance, evidence{}}],
crossref_codes[], risky_family{part_type, required_granularity, note_fa}|null, computed_at
```

### Produced — `vehicles.changed.v1` (compacted, key: `vehicle_slug`)
```
vehicle_slug, brand, model, trim, year_from, year_to, engine_code,
display_name_fa, aliases[], is_published, updated_at
```
Emit the **whole tree** on first boot so new consumers can bootstrap, and one message per edit thereafter. Tombstone on delete.

### Produced — `crossrefs.changed.v1` (compacted, key: `{code_a}|{code_b}`, `code_a < code_b`)
```
code_a, code_b, brand_a, brand_b, confidence, provenance, updated_at
```

---

## Architectural decisions already made (do not revisit)

| Decision | Value |
|---|---|
| Vehicle tree | **Built by hand**, not inferred |
| Fitment mapping | **Inferred from our own data** (seller titles), not manufacturer catalogs |
| Granularity | **Model + trim**, e.g. "206 Type 5" |
| Status | **Tri-state**: compatible / incompatible / unknown — never boolean |
| Plate/VIN lookup | Not available. Do not attempt it |

---

## Part one: The vehicle tree (manual)

Roughly 80–120 rows. Ship as a **YAML seed file** loaded by an idempotent management command. If you infer it from crawled data you will get a thousand spellings of "Peugeot Pars".

| Family | Trims |
|---|---|
| Peugeot 206 | Type 2, 3, 5, 6; SD (صندوق‌دار) V8, V9, V20 |
| Pride / Tiba | 111, 131, 132, 141, 151; Tiba 1, Tiba 2 |
| Samand / Pars | Samand LX, EF7, Soren; Pars ELX, Pars TU5 |
| Peugeot 405 | GLX, SLX, GL |

Each row: slug, brand, model, trim, year range, engine code, `display_name_fa`, and an `aliases` array with **every spelling a seller might use** — with and without ZWNJ, Persian and Latin digits, common misspellings. **The aliases array is what makes matching work; invest in it.**

`is_published` starts `false`; a vehicle publishes only when coverage clears the gate.

## Part two: Vehicle resolution

`resolve_vehicle(text) -> vehicle_slug | None`. Normalize input with the same Persian rules `enricher` uses (copy them into your service — do not import across services). Match `display_name_fa` and `aliases`, longest match wins.

Return the **most specific** match. A title saying only "206" resolves to the model level and produces trim-level `unknown` — **never guess a trim.** Confidently wrong fitment is worse than no fitment.

## Part three: Inference rules

**Rule 1 — consensus, not single claims.** Aggregate across all offers sharing a normalized `part_number` (or, absent one, the same brand + part type):

| Agreeing sellers | Result |
|---|---|
| ≥ 5 | `compatible`, high confidence, provenance `consensus` |
| 2–4 | `compatible`, medium confidence |
| 1 | `unknown`, low confidence, flagged |

**Rule 2 — reject over-broad claims.** When `overbroad_claim` is set and it is the only evidence, the result is `unknown`, **never** `compatible`. Maintain the pattern list in config.

**Rule 3 — risky part families.** Seed a table of part types where model + trim is insufficient:

| Family | Required granularity | Why |
|---|---|---|
| Headlights, tail lights, mirrors | year | facelift variants differ |
| Head gasket, pistons, valves, timing | engine | engine code determines fit |
| Sensors (crank, cam, oxygen, ABS) | engine or year | supplier changes mid-generation |
| ECU and wiring | year + engine | |
| Windshield and glass | year | |

~15 rows. If required granularity is absent, status is `unknown` **and** `risky_family` is attached so downstream shows the `note_fa` warning. Small list, catches most "it didn't fit" returns — do not skip it.

**Rule 4 — human corrections win.** `review.decided` events with fitment subjects override computed results permanently. Store them and re-apply after any recomputation.

## Part four: Cross-references

Build `code_a ↔ code_b` equivalences from three signals: co-occurrence in titles (sellers write "کد X معادل Y"), shared fitment plus shared part type plus similar price band across brands, and manual entry through the admin.

Normalize pair ordering so each pair is stored once. **A cross-reference is a display hint, not an identity claim** — `matcher` must never merge across brands because of one, and it is told so in its own spec.

## Part five: Launch gate

A vehicle publishes only when **≥70% of active offers** whose part type is relevant to it have a non-`unknown` verdict. Compute periodically, expose as a metric, and refuse to publish below threshold. Publishing an under-covered vehicle produces empty search results and users conclude we don't stock the part.

---

## Django models

`Vehicle`, `PartFitment` (offer_uid, vehicle, status, confidence, provenance, evidence — unique on the pair), `CrossRef`, `RiskyPartFamily`, `OfferReadModel` (local copy from `offers.enriched`), `HumanCorrection`, `ProcessedEvent`.

**Admin for `Vehicle`, `CrossRef`, and `RiskyPartFamily` is a deliverable**, not optional. Editing a vehicle must emit a `vehicles.changed` event.

---

## Project layout

```
services/fitment/
├── Dockerfile  requirements.txt  docker-compose.yml  Makefile  README.md
├── manage.py
├── contracts/
│   ├── consumed/{yadakchi.offers.enriched.v1,yadakchi.review.decided.v1}.json
│   └── published/{yadakchi.offers.fitted.v1,yadakchi.vehicles.changed.v1,yadakchi.crossrefs.changed.v1}.json
├── src/fitment/
│   ├── settings.py  models.py  admin.py
│   ├── text.py            # local copy of Persian normalization
│   ├── resolver.py  inference.py  crossref.py  coverage.py  producer.py
│   ├── seed/{vehicles,risky_families,overbroad_patterns}.yaml
│   └── management/commands/{consume_offers,consume_decisions,seed_vehicles,recompute,emit_reference}.py
└── tests/
```

Compose: this service plus Postgres, Redis, Kafka. No other service.

---

## Acceptance criteria

1. Seeding is idempotent; editing an alias updates the row and emits one `vehicles.changed` event.
2. `resolve_vehicle` correctly handles 50+ real hint strings, including Persian/Latin digit mixes and ZWNJ variants.
3. A bare "206" resolves to model level and yields trim-level `unknown` — never a guessed trim.
4. An over-broad claim as sole evidence never yields `compatible`.
5. Five agreeing sellers yield `compatible` with provenance `consensus`; one seller yields `unknown`.
6. A headlight offer without year evidence yields `unknown` with `risky_family` attached.
7. A human correction survives a full recomputation.
8. Coverage below 70% refuses publication and emits the metric.
9. Cross-reference pairs are stored once regardless of input order.
10. A new consumer reading `vehicles.changed` from the beginning reconstructs the full tree.
11. Consuming the same offer event twice yields identical fitment rows and one emitted event.
12. `mypy`, `ruff`, tests, `check-contracts` pass.

## Explicitly out of scope

Merging offers into products. Filtering search results. Any use of manufacturer catalog data.

## Warnings

- **Never collapse tri-state into boolean.** Downstream, `unknown` means "show with a caveat", not "incompatible". Collapsing it makes most of the catalog invisible — a documented compound risk.
- **Never infer a trim that wasn't stated.**
