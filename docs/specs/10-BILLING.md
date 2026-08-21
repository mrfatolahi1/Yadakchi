# 10 — `billing` service

**Build order: EIGHTH.** After `catalog`. Parallel with `search`.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`, `02-EVENT-CONTRACTS.md`.
**You own:** `services/billing/` and nothing else.

---

## What this service is

Sends the user to the seller's site, counts the click, charges the seller's wallet, and rejects fraud. This is where revenue is measured — correctness matters more than elegance.

**Stack: Django 5 + Django Ninja.** Two HTTP surfaces in one project: a **public redirect endpoint** and an **internal API** for `ops`.

**This service must be able to be the only thing still standing.** If everything else is down but redirects work, revenue and seller trust survive. Consequences:

- Minimal dependencies on the hot path. No Typesense, no templates, no calls to other services.
- **If Postgres is slow or unavailable, queue the click in Redis and complete the redirect anyway.** Never make the user wait on billing. A drain worker reconciles into Postgres.
- Independent health endpoint.
- Run the redirect route with a stripped middleware stack — no session, no auth, no locale middleware.

---

## How it connects

| Direction | Peer | Channel |
|---|---|---|
| **consumes** | `catalog` | Kafka `yadakchi.sellers.changed.v1` *(compacted)* — local seller read model |
| **produces** | `catalog`, `matcher`, `crawler` | Kafka `yadakchi.clicks.recorded.v1` |
| **produces** | `catalog` | Kafka `yadakchi.seller_billing.changed.v1` *(compacted)* — panel-offer display state |
| **produces** | `ops` | Kafka `yadakchi.review.requested.v1` — per-seller click-velocity anomalies |
| **serves** | end users | `GET /go/{token}` — public, never cached |
| **serves** | `ops` | internal API for the seller dashboard |
| owns | Postgres `yadakchi_billing`, Redis db 7 | |

**On `review.requested.v1`:** you produce to this topic but do not own its schema — `matcher` does. Hold a byte-identical `consumed/` copy; never place this file in `published/`. `make sync-contracts` puts it there for you, and `make check-contracts` fails the build if two services publish the same topic.

You **never** call `catalog` synchronously. Everything you need about a seller arrives on `sellers.changed`.

### Produced — `clicks.recorded.v1` (key: `product_uid`)
```
click_id, product_uid, offer_uid, seller_key, cost_toman, is_suspicious, occurred_at
```
Consumers use this only for traffic-derived priority. **Financial truth stays inside your database** — never let another service compute money from this topic.

### Internal API (Django Ninja)

| Endpoint | Purpose |
|---|---|
| `GET /v1/sellers/{seller_key}/stats` | clicks and spend over time. **Impressions are deferred to phase two** — no service emits them, and the two that witness one (`web`, `search`) have no path here. Do not invent a source; leave the field out rather than returning a fabricated number |
| `GET /v1/sellers/{seller_key}/wallet` | balance and transactions |
| `POST /v1/sellers/{seller_key}/topup` | record a top-up |
| `GET /v1/rates` | active CPC rate card |

Publish `openapi.json` to `contracts/published/`.

---

## Architectural decisions already made (do not revisit)

| Decision | Value |
|---|---|
| Revenue model | **CPC** |
| Rate | **Tiered by product price band** |
| Onboarding | **Open self-serve**, with earned visibility |
| Paid placement | **None.** Ranking is never purchasable |
| Crawled (non-panel) offers | **Displayed free, never removed** — catalog coverage depends on them |

---

## The redirect flow

```
user clicks an offer on the product page
  → GET /go/{token}
  → verify signature and freshness
  → take the destination from the verified token
  → 302 to seller
  → asynchronously: record click, evaluate fraud, charge wallet, emit clicks.recorded
```

The token is a **signed click intent**: `product_uid`, `offer_uid`, `seller_key`, `destination_url`, `price_toman`, `is_panel_offer`, `issued_at`, `nonce`, signed with `CLICK_SIGNING_KEY`. `web` mints it using the same shared secret; document the token format in your README so the `web` agent can implement it from your spec alone.

**Why `price_toman` and `is_panel_offer` are in the token.** The rate card is keyed by price band and charging depends on whether the offer is a panel offer, and you can look up neither at click time: `sellers.changed` carries no product pricing, `is_panel` is a property of the *seller* rather than of the offer, and you may not call `catalog`. Both facts are already in front of `web` — they are fields of the product payload it just rendered — so `web` signs them in and you read them from a token you have verified. A signed value is not user input: it cannot be tampered with without breaking the signature, and a tampered token is rejected before any of this is read.

Consequences to implement deliberately:

- **The price you charge on is the price the user saw.** It can be at most one token lifetime stale (default 30 minutes). That is the correct behaviour, not a tolerated flaw — billing a band the user was never shown would be indefensible to the seller.
- **`price_toman` may be `null`** when the offer had no usable price. Fall back to the lowest band rather than refusing the redirect; never block a user over a rate lookup.
- **`is_panel_offer` is per offer, not per seller.** A panel seller can also have crawled listings, so `is_panel` on `sellers.changed` cannot answer "is *this* offer chargeable". Only a panel offer is ever charged, and only panel offers are suspended on zero balance; crawled offers are free and stay visible. Trust the token's flag, not the seller read model.
- Both fields are **inside the signature**. Reject the token if either is absent — an unsigned or missing price is a pricing decision made by the caller.

- Reject tokens older than a short window (default 30 minutes).
- Reject replayed nonces within the window (Redis set).
- **Never accept a destination URL as a query parameter.** The destination is inside the signed token only. Open redirects are an easy and serious vulnerability — cover this with a security test.
- Never cache this route.

## Anti-fraud — required from day one

A competitor can burn a seller's daily budget in minutes.

| Rule | Behaviour |
|---|---|
| Per-IP rate limit per seller | beyond N in a window → mark suspicious, do not charge |
| Per-fingerprint (IP + UA hash) repeat window | repeat click on the same offer within N minutes is free |
| Known bot user agents | never charged |
| Missing or foreign referer | flagged |
| Velocity anomaly per seller | emits `review.requested` |

**Suspicious clicks are still redirected and still recorded** — they are simply not charged and are flagged. Never block a real user because a heuristic fired. **Hash IPs and user agents; never store raw values.**

## Tiered CPC

A rate card keyed by product price band. A 50,000-toman filter cannot sustain a 300-toman click; a 3,000,000-toman clutch kit can sustain several times that.

Resolve the rate at click time and **freeze `cost_toman` onto the click record**, so changing the rate card never alters historical billing.

## Wallet

Prepaid. Every charge writes a transaction with `balance_after_toman` for auditability.

- **Charging must be atomic and idempotent**, keyed on `click_id`. A retried drain task must never double-charge.
- On zero balance: the seller's **panel** offers stop being displayed. Emit `yadakchi.seller_billing.changed.v1` with `panel_offers_active: false` and `suspension_reason: "zero_balance"` so `catalog` can rebuild affected products, and notify the seller. Emit it again with `true` and a null reason on top-up. It carries the display consequence only — **never** a balance, a spend figure or a transaction; financial truth does not leave this service.
- **Crawled non-panel offers are never charged and never suspended.** Free listing keeps catalog coverage alive.
- Top-up through the domestic gateway; record the reference. The gateway integration may be stubbed initially.

## The open business question

An unresolved product decision, documented in the architecture doc: if crawled offers display free while panel sellers pay CPC, what is the incentive to join?

Working proposal your implementation must support: crawled offers stay free but **without control**, with lower freshness and a **capped trust ceiling**; panel membership grants self-managed accurate data, real-time updates, the dashboard, and the ability to rise. Implement the non-panel trust cap as a configurable value so it is easy to change when the decision is finalized.

---

## Django models

`Seller` (local read model from `sellers.changed`, plus wallet balance which is **yours**), `ClickEvent`, `WalletTransaction`, `CpcRate`, `SuspicionRule`, `ProcessedEvent`. No impression model — see the stats endpoint. Admin for rates and manual adjustments.

---

## Project layout

```
services/billing/
├── Dockerfile  requirements.txt  docker-compose.yml  Makefile  README.md
├── manage.py
├── contracts/
│   ├── consumed/{yadakchi.sellers.changed.v1,yadakchi.review.requested.v1}.json
│   └── published/{yadakchi.clicks.recorded.v1,yadakchi.seller_billing.changed.v1,openapi}.json
├── src/billing/
│   ├── settings.py  models.py  admin.py  api.py
│   ├── redirect_view.py     # stripped middleware, hot path
│   ├── tokens.py            # sign/verify — document the format in README
│   ├── fraud.py  wallet.py  rates.py  reporting.py  producer.py
│   └── management/commands/{consume_sellers,drain_clicks,reconcile,fraud_report}.py
└── tests/test_security.py
```

Compose: this service plus Postgres, Redis, Kafka.

---

## Acceptance criteria

1. A valid token 302s to the correct seller URL in **under 50ms with Postgres deliberately stalled**, proving the Redis fallback path.
2. Tampered, expired, or replayed tokens return 400 and charge nothing.
3. A destination URL supplied as a query parameter is ignored — explicit security test, no open redirect.
4. Retrying the drain task never double-charges — duplicate-delivery test.
5. Exceeding the per-IP limit marks clicks suspicious, skips the charge, and still redirects.
6. A known bot user agent is never charged.
7. Zero balance suspends panel offers and emits `seller_billing.changed` with `panel_offers_active: false`; **crawled non-panel offers remain visible**. A top-up re-emits it as `true`.
8. CPC rate is resolved from the price band and frozen; changing the card does not alter historical charges.
9. Seller stats endpoints return correct figures against seeded data.
10. Nightly reconciliation finds zero discrepancies on a seeded day.
11. `clicks.recorded` validates against the published schema and is emitted exactly once per click.
12. `mypy`, `ruff`, tests, `check-contracts` pass.

## Explicitly out of scope

Rendering the seller dashboard UI (`ops` does that from your API). Influencing search or seller ranking — **never**. Product data.

## Warnings

- **Never let billing influence ranking.** No boost for high-CPC sellers. This line protects the entire product's credibility.
- **Never block the redirect on billing work.**
- **Never store raw IPs.**
