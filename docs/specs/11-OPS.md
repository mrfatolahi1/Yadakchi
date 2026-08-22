# 11 — `ops` service

**Build order: NINTH.** But see the warning below — **do not defer it past `matcher` shipping.**
**Prerequisite reading:** `00-PROJECT-BRIEF.md`, `02-EVENT-CONTRACTS.md`.
**You own:** `services/ops/` and nothing else.

---

## Warning about ordering

This service is listed late, but `matcher`'s human-arbiter guardrail is **only real if this tool exists**. Without a review queue, aggressive merging ships with no mitigation and the documented compound risk materializes immediately.

Ship the **merge review queue** as soon as `matcher` lands. The rest can follow.

---

## What this service is

The internal console for humans. Not customer-facing. Different audience, different security model, different SLA.

Four jobs: **merge review queue**, **data health dashboards**, **dictionary approval**, and the **seller panel**.

**Stack: Django 5 + Django admin + Jinja/Django templates + HTMX.** No separate frontend build. This is where Django's admin pays for itself.

---

## How it connects

| Direction | Peer | Channel |
|---|---|---|
| **consumes** | `matcher`, `crawler`, `fitment`, `billing`, `enricher` | Kafka `yadakchi.review.requested.v1` |
| **consumes** | `catalog` | Kafka `yadakchi.products.changed.v1`, `yadakchi.sellers.changed.v1` *(compacted)* |
| **produces** | `matcher`, `fitment`, `search` | Kafka `yadakchi.review.decided.v1` *(compacted, infinite retention)* |
| **calls** | `catalog` | HTTP — product detail for context |
| **calls** | `billing` | HTTP — seller stats, wallet, top-up |
| owns | Postgres `yadakchi_ops`, Redis db 7 (from `OPS_REDIS_URL`) | |

### Consumed — `review.requested.v1`

```
request_uid, kind, priority, subject{}, evidence{}, requested_at
```

`kind` ∈ `merge_pair | split_product | adapter_broken | synonym_candidate | price_ambiguous`.

**`evidence` is self-sufficient by contract.** You must be able to render the review screen from the event alone. Calling `catalog` is for extra context only, never a hard dependency — a review must still be decidable if `catalog` is down.

### Produced — `review.decided.v1` (key: `request_uid`)

```
request_uid, kind, decision, subject{}, actor, reason, decided_at
```

`decision` ∈ `same_product | different_products | approve | reject | skip`.

**This topic is compacted with infinite retention.** It is the permanent record of human judgment. `matcher` and `fitment` replay it last after any recomputation, and it overrides everything computed.

---

## Access model

- Its own container, its own port, **behind VPN or strong authentication.** Never exposed publicly.
- Two roles: `staff` (full) and `seller` (own data only, seller panel section only).
- Every mutating action records the actor.

---

## Part one: Merge review queue

The most important screen in the product.

**Ordered by `priority` from the event, which is traffic-derived — never FIFO.** A reviewer working FIFO spends the day on products nobody looks at. Roughly 200 high-traffic products capture most clicks; reviewing those makes one human sufficient.

For a `merge_pair`, render side by side from `evidence`: both titles (raw and normalized), brands, part numbers, part types, pack quantities, authenticity claims, both prices, **the resulting price range if merged**, fitment sets, images, the model's `reason_fa`, and the computed score with the features that drove it.

Actions: **same product** / **different products** / **skip** / **needs a contract change**.

Requirements:

- **Keyboard-first.** A reviewer should clear 200 items a day: single-key actions, no mouse, no page reload between items. HTMX makes this straightforward.
- Show whether a human already decided this pair — decisions are sticky and must never be silently re-queued.
- Split tooling: open a product, select member offers, split them out — emitted as a `split_product` decision.
- **Every decision emits `review.decided`.** `matcher` turns those into golden-dataset labels automatically. Reviewing is the cheapest labeling you will ever get.

## Part two: Data health dashboards

Read-only views over metrics other services already export, plus your own event stream. **Display; do not recompute.**

| Screen | Shows |
|---|---|
| Adapter health | parse rate per source vs baseline, open `adapter_broken` items |
| Pipeline throughput | Kafka consumer lag per service, DLQ contents with a retry action |
| Match quality | precision/recall trend, **singleton ratio**, average offers per product |
| Coverage | fitment coverage per vehicle against the 70% gate |
| SEO | indexed-to-submitted ratio against the 60% tripwire |
| AI usage | daily budget consumption, cache hit rate |

Each screen must make the **architecture tripwires visible with their thresholds marked**, since crossing one triggers a documented decision change.

## Part three: Dictionary approval

Synonym candidates arrive as `review.requested` with `kind: synonym_candidate`, carrying evidence (co-occurrence counts, example titles). Approve or reject with one keystroke.

**Only approved terms flow into search query expansion.** Candidates serve as ranking signals only. Make this distinction visible in the UI so nobody approves carelessly. Maintain an easy-to-extend rejection list.

## Part four: Seller panel

Rendered here; data from `billing`'s API and `catalog`'s product data.

For a logged-in seller: clicks and spend over time (**impressions are deferred to phase two** — `billing` has no source for them, so do not show the column); **price rank per product** — "your price ranks 7th on this item"; comparison against the market median; wallet balance, transactions, top-up; and for panel members, offer management.

Also show their tier and **what would raise it** (price accuracy, stock accuracy), so earned visibility is legible rather than mysterious.

**This screen is the sales tool** that converts a crawled seller into a paying panel member. It is the only surface in this service that deserves real polish.

## Part five: Manual data entry

Source registry adjustments, cross-reference pairs, and risky part families are edited in `fitment` and `crawler` admins — **link to them, do not duplicate them here.** You own only what lives in your database.

---

## Django models

`ReviewItem` (request_uid, kind, priority, subject, evidence, status, assigned_to, resolved_at), `Decision`, `ProductReadModel`, `SellerReadModel`, `SynonymCandidate`, `ProcessedEvent`.

---

## Project layout

```
services/ops/
├── Dockerfile  requirements.txt  docker-compose.yml  Makefile  README.md
├── manage.py
├── contracts/
│   ├── consumed/{review.requested,products.changed,sellers.changed}.json
│   └── published/yadakchi.review.decided.v1.json
├── src/ops/
│   ├── settings.py  models.py  admin.py  auth.py
│   ├── views/{review,health,dictionary,seller}.py
│   ├── clients/{catalog,billing}.py
│   ├── producer.py
│   ├── templates/  static/
│   └── management/commands/consume_*.py
└── tests/
```

Compose: this service plus Postgres, Redis, Kafka, and stubbed `catalog`/`billing` HTTP.

---

## Acceptance criteria

1. The queue orders by the event's traffic-derived priority, verified with seeded events.
2. A reviewer processes 20 seeded items using only the keyboard, with no full page reloads.
3. Every decision emits exactly one `review.decided` event validating against the published schema.
4. The review screen renders **entirely from `evidence`** with `catalog` unreachable.
5. The prospective merged price range is displayed before the decision.
6. A pair already decided is shown as decided and is not re-queued.
7. Every tripwire (match precision, singleton ratio, fitment coverage, index ratio, AI budget) is visible with its threshold marked.
8. Approving a synonym emits a decision `search` can act on; a candidate does not.
9. A seller sees only their own data; cross-account access returns 403.
10. Price rank shown to a seller matches the public product ordering.
11. Consuming the same `review.requested` twice creates one queue item.
12. `mypy`, `ruff`, tests, `check-contracts` pass.

## Explicitly out of scope

Recomputing metrics — display only. Public-facing pages. Changing matching logic. Writing to any other service's database — you have no credentials for one.

## Warnings

- **Do not build a heavy admin framework.** Django admin plus a handful of custom HTMX views is faster to build and easier for an agent to extend.
- **Do not skip emitting `review.decided`.** It is the single highest-value side effect in the system — it both corrects the data and grows the golden dataset.
- **Do not make review depend on other services being up.**
