# 07 — `matcher` service

**Build order: SIXTH.** After `fitment`. **This is the longest and most important service in the project.**
**Prerequisite reading:** `00-PROJECT-BRIEF.md`, `02-EVENT-CONTRACTS.md`.
**You own:** `services/matcher/` and nothing else.

---

## What this service is

Decides which Offers describe the **same physical product**, and clusters them.

If this works, the product works. If it doesn't, no amount of frontend polish saves it — every other service is plumbing around this one.

**Stack: Django 5 + numpy + pgvector + Celery.** Django for models, migrations, and management commands; Celery for batch recomputation; pgvector for auxiliary similarity search.

---

## How it connects

| Direction | Peer | Channel |
|---|---|---|
| **consumes** | `enricher` | Kafka `yadakchi.offers.enriched.v1` |
| **consumes** | `fitment` | Kafka `yadakchi.offers.fitted.v1`, `yadakchi.vehicles.changed.v1` *(compacted)*, `yadakchi.crossrefs.changed.v1` *(compacted)* |
| **consumes** | `ops` | Kafka `yadakchi.review.decided.v1` *(compacted, infinite)* — **human decisions** |
| **consumes** | `billing` | Kafka `yadakchi.clicks.recorded.v1` — traffic signal for review priority |
| **produces** | `catalog` | Kafka `yadakchi.clusters.changed.v1` |
| **produces** | `ops` | Kafka `yadakchi.review.requested.v1` — `kind: merge_pair` |
| **calls** | `ai` | HTTP `POST /v1/judge`, `POST /v1/embed` |
| owns | Postgres `yadakchi_matcher` (pgvector), Redis db 4 | |

You maintain **local read models** of offers, fitments, vehicles, cross-references, human decisions, and click counts. You never call another service to fetch them.

### Produced — `clusters.changed.v1` (key: `cluster_uid`)
```
cluster_uid, members[{offer_uid, confidence, provenance}],
change_reason, predecessor_uids[], successor_uid|null, computed_at
```
You **mint `cluster_uid`** as a UUIDv4. `catalog` adopts it as `product_uid` unchanged.

---

## Architectural decisions already made (do not revisit)

| Decision | Value |
|---|---|
| Candidate generation | **Rule-based composite key** (brand + part type + vehicle), not embedding-first |
| Ambiguity arbiter | **Human review queue** |
| Error bias | **Aggressive** — when in doubt, merge |

### The compound risk you exist to mitigate

Those three plus one decision in `enricher` (authenticity is never verified) combine badly. "Aggressive" means ambiguous cases merge automatically; "human queue" means the arbiter cannot keep up. Result: a genuine brake pad and a cheap copy land on one page, the price range widens 4x, and the "cheapest" option is a copy the customer buys believing it is genuine.

**The three guardrails below are the entire mitigation. Build them first.**

---

## Guardrail 1 — Brand is a hard wall

Two offers with **different normalized brand strings are never merged.** Regardless of score, regardless of aggression, regardless of what `ai` says.

- Aggressive *within* a brand. Conservative *across* brands.
- A `brand: null` offer may join a branded cluster only on an exact part-number match with very high score.
- A cross-reference says two part numbers are equivalent — **that is a display hint, never a merge instruction.**

Enforce it as a filter in blocking **and** re-assert it as an assertion before writing any membership. Cost near zero; removes the largest failure mode.

## Guardrail 2 — Price circuit breaker

Before committing a merge, compute the resulting in-stock price range. If it would exceed **2.5× the median** of the resulting cluster, **do not auto-merge** — emit `review.requested` instead.

Threshold in config (`MERGE_PRICE_RANGE_FACTOR`), not hardcoded — it needs calibration against real data. This is a statistical circuit breaker that catches genuine-vs-copy merges without knowing which is which.

## Guardrail 3 — Traffic-prioritized review

A FIFO queue is useless — the reviewer spends the day on products nobody looks at. Set `priority` on every `review.requested` from your local click read model (fall back to offer count early on). Recompute on a schedule; do not fix it at insert.

Roughly 200 high-traffic products capture most clicks. If those are reviewed, one human arbiter is enough.

---

## Stage 1 — Blocking

**Primary key:** `normalize(brand) | part_type | vehicle_group`, where `vehicle_group` is the offer's compatible vehicle slugs, sorted and canonicalized. Offers with no fitment land in an `unmapped` group that blocks against everything within the same brand and part type.

**Additional channels (union, not intersection):** exact `part_number`; cross-referenced part number **within the same brand only**; `pg_trgm` similarity on `title_normalized` within brand.

**Embeddings are auxiliary, not primary.** Store in pgvector and use similarity to *recover* candidates the rules missed. Monitor whether the channel earns its keep.

### The known blind spot

Rule-based blocking means an offer whose brand or part type `enricher` failed to extract generates **no candidates at all** and sits alone forever.

**Mandatory metric: `yadakchi_matcher_singleton_ratio`** — share of active offers in single-member clusters. Alert on a rising trend. This is how you see extraction degrading.

## Stage 2 — Pairwise scoring

Features: part number match (exact / crossref / none — dominant), title similarity (token-set and character-level), brand match, part type match, **pack quantity match**, **authenticity claim match**, fitment overlap (Jaccard), price log-ratio, embedding cosine.

**Decision ladder:**

1. Exact part number + same brand + same pack quantity → merge, provenance `rule`. No model call.
2. Score above the high threshold → merge, provenance `rule`.
3. Score below the low threshold → reject.
4. **Between thresholds → `ai POST /v1/judge`.** Store the returned `reason_fa`.
5. Model confidence below its threshold → `review.requested`, and per the aggressive bias, provisionally merge **only if guardrails 1 and 2 both pass**.

Thresholds live in config and are tuned against the golden dataset, never guessed.

## Stage 3 — Clustering

Connected components over accepted pairs, with **transitivity guards**: if any pair inside a component was explicitly rejected, split the component rather than letting transitivity override the rejection.

Each component is one cluster with a stable `cluster_uid` that never changes. On split, set `successor_uid` on the retired cluster and list `predecessor_uids` on the new ones — `catalog` turns this into 301 redirects. **Never silently drop a cluster_uid.**

## Stage 4 — Human decisions are sticky

`review.decided` is compacted with infinite retention. Rules:

- A full recomputation **replays this topic last** and human decisions override everything computed.
- Before writing any automated membership, check for a conflicting human decision and yield to it.
- Implement this as an explicit guard with its own test. It is easy to lose in a refactor and expensive to notice.

---

## The golden dataset and evaluation harness

**The most important engineering asset in the project.** Treat it as product code.

- `GoldenPair` (offer_uid_a, offer_uid_b, is_same, labeled_by, labeled_at). Target a few thousand, stratified: easy positives, easy negatives, and above all **hard cases near the threshold**.
- Rows arrive automatically from `review.decided` — every human review is free labeling. Also provide export/import commands for bulk labeling.
- `evaluate` command computes precision, recall, F1 and writes a report.
- **CI gate: precision below 95% fails the build.** Every threshold or algorithm change must include before/after numbers in the pull request.

---

## Project layout

```
services/matcher/
├── Dockerfile  requirements.txt  docker-compose.yml  Makefile  README.md
├── manage.py
├── contracts/
│   ├── consumed/{offers.enriched,offers.fitted,vehicles.changed,crossrefs.changed,review.decided,clicks.recorded}.json
│   └── published/{clusters.changed,review.requested}.json
├── src/matcher/
│   ├── settings.py  models.py  admin.py
│   ├── blocking.py  features.py  scoring.py
│   ├── guardrails.py       # brand wall + price circuit breaker — visible and testable
│   ├── clustering.py  embeddings.py  ai_client.py  producer.py
│   ├── golden.py  evaluate.py
│   ├── tasks.py            # Celery: recompute, evaluate, priority refresh
│   └── management/commands/{consume_*,recompute,evaluate,export_labels,import_labels}.py
└── tests/{test_guardrails,test_golden_eval}.py
```

Compose: this service plus Postgres (pgvector), Redis, Kafka, and a stubbed `ai`.

---

## Acceptance criteria

1. **Precision above 95%** on the golden dataset. This is the phase-one gate for the entire project.
2. Different brands are never merged — including when `ai` says same and when a cross-reference links their part numbers.
3. A merge that would breach the price range factor is refused and emits `review.requested`.
4. A human decision from `review.decided` survives a full recomputation.
5. An explicit pair rejection is not overridden by transitivity.
6. A split emits `clusters.changed` with `successor_uid` and `predecessor_uids` populated.
7. Review priority reflects click volume, verified with seeded click events.
8. `yadakchi_matcher_singleton_ratio` is exported and rises when brand extraction is degraded in a fixture.
9. Consuming the same offer event twice creates no duplicate membership and emits one cluster event.
10. Bootstrapping from empty by replaying all topics from the beginning reproduces the same clusters.
11. Average offers per cluster on a realistic seeded dataset is meaningfully above 1.
12. `evaluate` runs in CI and fails below threshold.
13. `mypy`, `ruff`, tests, `check-contracts` pass.

## Explicitly out of scope

Product display fields, titles, images (`catalog`). The review UI (`ops`) — you emit requests, ops renders them. Verifying authenticity.

## Warnings

- **Do not soften the brand wall.** When you see two obviously identical parts under different brand strings, the fix is a brand-normalization alias, not a cross-brand merge.
- **Do not skip the golden dataset to move faster.** Without it every later change is a blind guess.
- **Do not call the model on every pair.** The ladder exists to keep it on the hard minority.
