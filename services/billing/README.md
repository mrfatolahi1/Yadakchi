# billing

Billing owns outbound redirects, CPC accounting, seller wallets, and fraud flags. It never
influences ranking and never calls another Yadakchi service synchronously.

## Signed click token

`web` creates the token used by `GET /go/{token}`. The format is:

```text
base64url(payload_json_without_padding) + "." +
base64url(HMAC-SHA256(CLICK_SIGNING_KEY, encoded_payload)_without_padding)
```

The HMAC input is the ASCII base64url payload segment exactly as sent. Compact JSON with sorted
keys is recommended but not required. The payload has exactly these fields; unknown or missing
fields are rejected:

```json
{
  "product_uid": "93c9da93-7ffb-498e-afc1-2798ea05112e",
  "offer_uid": "ad1e2af57f36691329247db654602a4e",
  "seller_key": "yadakyar",
  "destination_url": "https://seller.example/parts/123",
  "price_toman": 1250000,
  "is_panel_offer": true,
  "issued_at": 1787292000,
  "nonce": "a-cryptographically-random-base64url-value"
}
```

- `issued_at` is a Unix timestamp in seconds. Tokens expire after 30 minutes by default.
- `price_toman` is an integer or `null`; `null` uses the lowest active CPC band.
- `is_panel_offer` is per offer. Crawled offers are never charged or suspended.
- `destination_url`, price, and offer type exist only inside the signature. Query parameters do
  not override them.
- Nonces are single-use during the token lifetime.

The production ASGI dispatcher handles `/go/` and `/healthz` before Django, bypassing sessions,
authentication, CSRF, locale, and all other Django middleware. A redirect performs token checks
and one atomic Redis operation; it never touches Postgres or Kafka.

## Wallet semantics

Charges are atomic and keyed by `click_id`. CPC is all-or-nothing: if a wallet has less than the
full resolved CPC, the click costs zero, the residual balance is preserved, and panel offers are
suspended with `insufficient_balance`. A balance that reaches exactly zero is suspended with
`zero_balance`. Top-ups are idempotent by gateway reference and reactivate panel offers.

Rate-card rows are historical versions. Close an existing row with `effective_to` and create a
new row; do not edit a prior row's price band or cost. Delayed Redis drains resolve the version
that was effective at the click's `occurred_at`, then freeze that cost on `ClickEvent`.

## Local development

```bash
cp .env.example .env
set -a
source ../../platform/.env
source ./.env
set +a
docker network inspect yadakchi >/dev/null 2>&1 || docker network create yadakchi
docker compose up --build -d postgres redis kafka kafka-init billing-migrate billing billing-drain billing-seller-consumer
curl http://localhost:${BILLING_HOST_PORT:-8010}/healthz
docker compose exec billing make check
```

The service compose includes `platform/docker-compose.infra.yml`. Listing the target services in
the command starts only billing and its Postgres, Redis, and Kafka dependencies; no other
Yadakchi application service is required.

The Redis database assigned by `BILLING_REDIS_URL` is used for nonce guards, fraud windows, and
the click queue; the database number is never hardcoded by this service. Postgres remains the
financial source of truth. Kafka publishing uses a durable database outbox.

Required production environment variables are `DJANGO_SECRET_KEY`, `CLICK_SIGNING_KEY`,
`PRIVACY_HASH_KEY`, `INTERNAL_API_TOKEN`, `DATABASE_URL`, `BILLING_REDIS_URL`, and
`KAFKA_BOOTSTRAP_SERVERS`.

## Commands

- `python manage.py consume_sellers` consumes `yadakchi.sellers.changed.v1`; offsets commit only
  after the database transaction commits.
- `python manage.py drain_clicks` drains Redis idempotently and publishes the durable outbox.
- `python manage.py reconcile --day YYYY-MM-DD` reconciles click spend and wallet charges.
- `python manage.py fraud_report --day YYYY-MM-DD` emits a structured per-seller fraud report.
- `make openapi` updates `contracts/published/openapi.json`.
- `make check` runs formatting, linting, typing, tests, contract validation, and OpenAPI drift.
