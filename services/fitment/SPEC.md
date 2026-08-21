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
| **produces** | `ops` | Kafka `yadakchi.review.requested.v1` — `kind: fitment_conflict` |
| owns | Postgres `yadakchi_fitment`, Redis db 3 | |

**On `review.requested.v1`:** you produce to this topic but do not own its schema — `matcher` does. Hold a byte-identical `consumed/` copy; never place this file in `published/`. `make sync-contracts` puts it there for you, and `make check-contracts` fails the build if two services publish the same topic.

### Consumed — `offers.enriched.v1`
You need: `offer_uid`, `part_number`, `part_type`, `brand`, `vehicle_hints[]`, `vehicle_hints_excluded[]`, `overbroad_claim`, `title_normalized`.

`vehicle_hints` are vehicles the seller claims the part **does** fit; `vehicle_hints_excluded` are vehicles they claim it does **not**. That second list is the only source of claim polarity in the system and is what makes Rule 5 possible. It is optional on the wire — treat absent or empty as "no negative claim extracted", **never** as "fits everything", and never read a negative out of a vehicle's absence from `vehicle_hints`. Keep a **local read model of offers** built from this topic — you need it for consensus across sellers.

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

Ship as a **YAML seed file** loaded by an idempotent management command. If you infer it from crawled data you will get a thousand spellings of "Peugeot Pars".

| Family | Trims |
|---|---|
| Peugeot 206 | Type 2, 3, 5, 6; SD (صندوق‌دار) V8, V9, V20 |
| Pride / Tiba | 111, 131, 132, 141, 151; Tiba 1, Tiba 2 |
| Samand / Pars | Samand LX, EF7, Soren; Pars ELX, Pars TU5 |
| Peugeot 405 | GLX, SLX, GL |

Each row: slug, brand, model, trim, year range, engine code, `display_name_fa`, and an `aliases` array with **every spelling a seller might use** — with and without ZWNJ, Persian and Latin digits, common misspellings. **The aliases array is what makes matching work; invest in it.**

`is_published` starts `false`; a vehicle publishes only when coverage clears the gate.

### How many rows, and what you may assume

An earlier draft of this spec said "roughly 80–120 rows". That number predates the
settled granularity decision below it and conflicts with it; **the granularity
decision wins.** One row per model+trim is **~22 rows** for the four phase-one
families, and that is correct, not a shortfall.

`year_from`, `year_to` and `engine_code` are **descriptive attributes of a trim,
not a subdivision axis.** "Peugeot 206 Type 5" is one row with one production
range and one engine code — not four rows sliced by year. Do not manufacture
rows to reach a count.

**When a part genuinely needs a year or an engine to decide fitment, that is
handled by Rule 3, not by more vehicle rows.** `risky_family` and
`required_granularity` on `offers.fitted` exist exactly so the tree does not have
to explode combinatorially: the verdict becomes `unknown` and the buyer sees
`note_fa`. That mechanism is the answer to "this headlight differs by facelift
year" — a `peugeot-206-type-5-2009` slug is not.

You are authorised to assume, and should record the assumption in the seed file:

- **Production ranges** to the year, from the Iranian market's generally known
  build history. Where you are unsure of a boundary, widen the range rather than
  guessing a precise one — a too-wide range costs recall on a risky family, a
  wrong narrow one produces confidently wrong fitment.
- **`year_to: null`** for anything still in production or where the end is unclear.
- **`engine_code: null`** wherever a trim spans more than one engine. A null here
  is honest and is handled; a guessed code is not.
- **Additional trim rows** beyond the table when crawled titles show sellers
  routinely distinguishing one (a `131 SE` against a plain `131`). Add it as its
  own trim row with its own aliases — that is a real trim, not a subdivision.

**Do not invent data you cannot source.** Fewer correct rows beat more guessed
ones, and `is_published: false` plus the 70% coverage gate means an incomplete
tree degrades to "not launched yet" rather than to wrong answers. If the tree
needs a fact you do not have, leave the field null and raise it — the same rule
as everywhere else in this system.

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

The shape is fixed in spec 02 and is not yours to infer. For `kind: fitment_conflict`, `subject` is `{part_number, vehicle_slug, status}` and the verdict rides in **`subject.status`** — `compatible | incompatible | unknown` — because `decision` has five values across all kinds and cannot express a tri-state. Read the pair as:

| `decision` | what you do |
|---|---|
| `approve` | apply `subject.status` as a human-provenance verdict for that `(part_number, vehicle_slug)`. It overrides anything computed and survives recomputation. |
| `skip` | the human did not settle it. Leave the computed verdict; the item may be re-queued. |

A human-settled `unknown` is **not** a computed `unknown`. It is sticky, it means a person looked and the evidence genuinely does not decide, and it must stop that pair being queued again — otherwise your reviewer sees the same unanswerable item every week. Store the two kinds of `unknown` distinguishably. `reject`, `same_product` and `different_products` never appear on a `fitment_conflict`; if you receive one, dead-letter it rather than mapping it onto a verdict.

**Rule 5 — conflicting consensus goes to a human.** Rule 1 counts agreeing sellers; it says nothing about sellers who disagree. When the evidence for one part number and one vehicle points both ways — some sellers claim `compatible`, others `incompatible` — no majority makes that safe to decide automatically.

A seller "claims incompatible" when the vehicle resolves from an entry in their offer's **`vehicle_hints_excluded`**. That is the only admissible evidence of a negative claim. A vehicle simply missing from a seller's `vehicle_hints` is not disagreement — it is silence, and it contributes to neither side of the count. Three sellers listing the vehicle and two not mentioning it is a three-seller consensus under Rule 1, not a conflict; three listing it and two excluding it is the conflict this rule exists for. The verdict is `unknown`, and you emit `review.requested` with `kind: fitment_conflict` so a human can settle it. Include both sides in `evidence`: the offers on each side with their seller keys, titles and claims, the part number, and the `vehicle_slug` in dispute — `ops` renders the review screen from the event alone and must never query you for it. The resulting `review.decided` comes back under Rule 4 and sticks.

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
│   ├── consumed/{yadakchi.offers.enriched.v1,yadakchi.review.decided.v1,yadakchi.review.requested.v1}.json
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
