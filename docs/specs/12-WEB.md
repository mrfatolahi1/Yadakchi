# 12 — `web` service

**Build order: TENTH.** After `catalog`, `search`, and `billing`.
**Prerequisite reading:** `00-PROJECT-BRIEF.md`.
**You own:** `services/web/` and nothing else.

---

## What this service is

The public website: Persian, RTL, server-rendered, SEO-first.

**Stack: Next.js (App Router) + TypeScript.** This is the only non-Python service. It has **no database** — all data comes from two HTTP APIs.

**SEO is the primary acquisition channel for this business.** Every decision here is subordinate to that.

---

## How it connects

| Direction | Peer | Channel |
|---|---|---|
| **calls** | `catalog` | HTTP `GET /v1/products/{slug}`, `POST /v1/products/batch` |
| **calls** | `search` | HTTP `GET /v1/search`, `GET /v1/suggest`, `POST /v1/events/click` |
| **links to** | `billing` | `GET /go/{token}` — outbound clicks leave your app |
| **receives** | `catalog` | HTTP webhook → on-demand ISR revalidation |
| owns | nothing. **No database, no Kafka** | |

Both upstream services publish OpenAPI documents. **Generate your TypeScript types from them** (`openapi-typescript` or equivalent) and commit the generated types. Never hand-write the response shapes — that is how drift starts.

### Revalidation webhook

Expose `POST /api/revalidate` accepting `{ product_uid, slug, secret }`. `catalog` calls it whenever a product changes. This is **event-driven ISR** — the alternative, time-based revalidation across millions of pages, will kill the server. Verify the shared secret; rate-limit.

### Click tokens

Outbound clicks go to `billing`'s `/go/{token}`. The token is a signed click intent (`product_uid`, `offer_uid`, `seller_key`, `destination_url`, `issued_at`, `nonce`) signed with a shared secret. **The exact format is documented in `services/billing/README.md`** — implement it from there. Mint tokens **server-side only**; never expose the signing key to the browser.

---

## Architectural decisions already made (do not revisit)

| Decision | Value |
|---|---|
| Rendering | **ISR with on-demand revalidation** |
| Index scope | **Index everything** — with the thin-page mitigation below |
| Accounts | **Guest by default**, optional login |
| Domain logic in the frontend | **Forbidden** |

---

## The cacheability split — read this first

The most important structural rule here, and it follows from combining page caching with a client-side vehicle garage.

| Surface | Rendering | Cache |
|---|---|---|
| **Product page** | ISR, **vehicle-agnostic**, no personalization | globally cached, purged on change |
| **Listing / search with vehicle filter** | client-side | not cached |
| **"Fits your vehicle" banner** | client-side fragment fetched after load | not cached |

**If the vehicle filter is baked into the cached product page, you need one cache entry per vehicle and the cache explodes.** The product page is one document served identically to everyone; vehicle-specific messaging layers on afterwards.

## No domain logic in the frontend

"Which offer is cheapest", "how are sellers ranked", "does this fit" — all arrive pre-computed. `catalog` already marks `is_cheapest` and `rank_position`. **Render; do not decide.** The same numbers must appear in the HTML for search engines, be identical across surfaces, and live in exactly one place.

---

## Pages

| Route | Purpose | Cache |
|---|---|---|
| `/` | vehicle picker, popular part types, search | ISR |
| `/p/[slug]` | product page | ISR, on-demand revalidated |
| `/c/[part_type]` | part type listing | ISR |
| `/v/[vehicle_slug]` | vehicle hub | ISR |
| `/v/[vehicle_slug]/[part_type]` | **the long-tail SEO money page** | ISR |
| `/search` | results | client-rendered |
| `/garage` | manage saved vehicles | client-rendered |

### Product page content — the thin-page mitigation

Because the policy is "index everything", a page with one offer and nothing else is a domain-wide SEO liability. Every product page carries:

- Title, image, brand, part type, authenticity label
- Full seller list with price, stock, trust, and the **cheapest badge prominent even when trust-first ordering puts another seller first**, plus a visible sort toggle
- **Fitment section** — which vehicles it fits, with the unverified caveat and the risky-family warning (`risky_family_note_fa`) where present
- **Cross-referenced equivalents** — "also sold as code X". Genuinely useful and unique to us
- **Price history chart** from the `price_series` already in the payload — no extra call
- Related parts

If a product lacks this substance it should not have been published by `catalog`. Report it rather than shipping an empty page.

## SEO requirements

- **Structured data** on every product page: `Product` with `AggregateOffer` (low, high, currency, offer count) and individual `Offer` entries. Validate in a test.
- **Canonical URL** on every page. A retired product returns **301** to `successor_product_uid`'s slug — the API tells you.
- **Dynamic partitioned sitemaps**: index plus shards, with `lastmod` from `updated_at`, **prioritized** so crawl budget goes to good pages first.
- `robots.txt` allowing product, category, and vehicle pages; disallowing `/search`, `/go/`, `/api/`.
- Persian slugs, `lang="fa"`, `dir="rtl"`.
- Open Graph and Twitter cards.
- **Tripwire:** if indexed-to-submitted drops below 60%, the indexing policy changes to tiered noindex. Surface the inputs; the decision is not yours.

## Performance

Cached TTFB under 100ms; uncached product render under 300ms. Self-hosted Persian fonts with `font-display: swap`, minimal client JS, images lazy-loaded below the fold, no heavy state library.

## The garage

Guest by default — vehicles in a **cookie**, no account required. Optional login later; when a guest logs in, the guest garage merges into the account (define and test the merge rule).

The garage **never changes the cached product page HTML.** It drives a client-side fragment that adds the "fits your car" banner after hydration.

## Accessibility and RTL

Proper RTL with logical CSS properties, keyboard navigation, visible focus states, alt text, adequate contrast. Persian numerals in display text, Latin in inputs.

---

## Project layout

```
services/web/
├── Dockerfile  package.json  next.config.js  tsconfig.json  Makefile  README.md
├── docker-compose.yml
├── contracts/consumed/{catalog-openapi.json, search-openapi.json}
├── src/
│   ├── app/{page,p/[slug],c/[part_type],v/[vehicle_slug],search,garage}/
│   ├── app/api/revalidate/route.ts
│   ├── lib/{catalog-client,search-client,click-token,garage}.ts
│   ├── types/generated/        # from OpenAPI — do not hand-edit
│   └── components/
└── tests/
```

Compose: this service plus **stubbed `catalog` and `search`** serving fixture responses. You must be able to develop and test with no Python service running.

---

## Acceptance criteria

1. A product page renders fully server-side; with JavaScript disabled it still shows title, all offers with prices, fitment, and equivalents.
2. Second request is served from the ISR cache; the revalidation webhook evicts it.
3. **Two users with different garage vehicles receive byte-identical cached product HTML**; the difference appears only in the client-side banner.
4. Structured data validates as `Product` + `AggregateOffer`, and prices match what is displayed.
5. A retired product's slug returns **301** to its successor.
6. The product page makes **exactly one** `catalog` call.
7. Cached TTFB under 100ms measured locally.
8. Sitemap index and shards generate, contain only published products, with correct `lastmod`.
9. Search with a garage vehicle shows compatible products first and unknown-fitment below, marked, and renders the fallback banner when `fallback_applied` is true.
10. Click tokens are minted server-side only; the signing key never reaches the browser — asserted by a test scanning the client bundle.
11. TypeScript types are generated from the committed OpenAPI documents, not hand-written.
12. `tsc --noEmit`, `eslint`, `next build`, and tests pass.

## Explicitly out of scope

The redirect endpoint itself (`billing`). The internal console (`ops`). Computing prices, rankings, or fitment. **A mobile app — not in the MVP.** Price alerts — post-MVP.

## Warnings

- **Never personalize the cached product page.** It is the highest-value SEO surface and must stay globally cacheable.
- **Never compute "cheapest" in the frontend.** It must match the structured data exactly; `catalog` already flags it.
- **Never call `catalog` or `search` from the browser with credentials.** Server components only for authenticated paths.
