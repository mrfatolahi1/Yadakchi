"""The read API. `web` renders product pages from it; `ops` reads sellers.

The design constraint that shapes everything here: **a product page must cost
a bounded, small number of queries no matter how many offers it has.** The
ranked list, the price series, the vehicle arrays and the cross-references
are all materialised onto the product row at rebuild time, so serving a
20-offer product is one row read, not one plus twenty. Acceptance criterion 1
asserts that with a query counter rather than by eye.

The payload never varies by user or by vehicle — it has to stay globally
cacheable. Vehicle-specific messaging is `web`'s job, client side.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

from django.db import connection
from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI
from ninja.errors import HttpError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from catalog.events import iso_utc
from catalog.metrics import API_LATENCY
from catalog.models import Product, ProductOffer, ProductSlug, Seller
from catalog.schemas import (
    BatchRequest,
    BatchResponse,
    HealthOut,
    ProductOut,
    RedirectOut,
    SellerOut,
)

api = NinjaAPI(
    title="yadakchi catalog",
    version="1.0.0",
    description=(
        "Canonical products, sellers and price history. Read-only: everything "
        "this service learns arrives over Kafka, and everything it decides "
        "leaves over Kafka. `web` and `ops` are the only callers."
    ),
    urls_namespace="catalog",
    docs_url="/docs",
)


def _product_response(product: Product) -> dict[str, Any]:
    return ProductOut.from_product(product.document, product.related, product.seller_badges)


def _location(slug: str) -> str:
    """A percent-encoded redirect target.

    Slugs are Persian. A header value has to be ASCII, and Django will
    RFC-2047-encode a non-ASCII one into a `=?utf-8?b?...?=` blob that no
    browser follows — so the path is encoded here, where it is visible.
    """
    return f"/v1/products/{quote(slug, safe='')}"


@api.post(
    "/v1/products/batch",
    response=BatchResponse,
    tags=["products"],
    summary="Many products by identity",
    description=(
        "Hydration fallback for search results. `search` renders from its own "
        "index built off products.changed; this exists for the moment the "
        "index is behind, not as a routine dependency."
    ),
)
def get_products_batch(request: HttpRequest, payload: BatchRequest) -> BatchResponse:
    with API_LATENCY.labels(endpoint="batch").time():
        parsed: list[uuid.UUID] = []
        for raw in payload.product_uids:
            try:
                parsed.append(uuid.UUID(raw))
            except ValueError as exc:
                raise HttpError(400, f"not a UUID: {raw}") from exc

        # One query for the whole batch, however many were asked for.
        found = list(
            Product.objects.filter(product_uid__in=parsed).only(
                "product_uid", "document", "related", "seller_badges"
            )
        )
        seen = {str(p.product_uid) for p in found}
        return BatchResponse(
            products=[ProductOut(**_product_response(p)) for p in found],
            missing=[str(u) for u in parsed if str(u) not in seen],
        )


@api.get(
    "/v1/products/{slug}",
    response={200: ProductOut, 301: RedirectOut},
    tags=["products"],
    summary="Full product view by slug",
    description=(
        "Resolves current slugs, historical slugs and retired products. An "
        "old slug or a retired product answers 301 with the target, because a "
        "URL that has ranked must never become a 404."
    ),
)
def get_product(request: HttpRequest, slug: str, response: HttpResponse) -> Any:
    with API_LATENCY.labels(endpoint="get_product").time():
        # One query: the slug row and its product, joined.
        row = (
            ProductSlug.objects.select_related("product")
            .filter(slug=slug)
            .only(
                "slug",
                "is_current",
                "product__product_uid",
                "product__slug",
                "product__document",
                "product__related",
                "product__seller_badges",
                "product__successor_product_uid",
                "product__successor_slug",
            )
            .first()
        )
        if row is None:
            raise HttpError(404, "product not found")

        product = row.product
        redirect = _redirect_target(product, slug)
        if redirect is not None:
            response.status_code = 301
            response["Location"] = _location(redirect["redirect_to_slug"])
            response["Cache-Control"] = "public, max-age=3600"
            return 301, redirect

        response["Cache-Control"] = "public, max-age=300"
        return 200, _product_response(product)


def _redirect_target(product: Product, requested_slug: str) -> dict[str, Any] | None:
    """A 301 body, or None when the requested slug is the live one."""
    if product.successor_product_uid and product.successor_slug:
        return {
            "status": "retired",
            "product_uid": str(product.product_uid),
            "redirect_to_slug": product.successor_slug,
            "successor_product_uid": str(product.successor_product_uid),
        }
    if requested_slug != product.slug:
        return {
            "status": "renamed",
            "product_uid": str(product.product_uid),
            "redirect_to_slug": product.slug,
            "successor_product_uid": None,
        }
    return None


@api.get(
    "/v1/products/by-uid/{product_uid}",
    response={200: ProductOut, 301: RedirectOut},
    tags=["products"],
    summary="Full product view by identity",
)
def get_product_by_uid(request: HttpRequest, product_uid: str, response: HttpResponse) -> Any:
    with API_LATENCY.labels(endpoint="get_product_by_uid").time():
        try:
            parsed = uuid.UUID(product_uid)
        except ValueError as exc:
            raise HttpError(400, "product_uid must be a UUID") from exc

        product = (
            Product.objects.filter(product_uid=parsed)
            .only(
                "product_uid",
                "slug",
                "document",
                "related",
                "seller_badges",
                "successor_product_uid",
                "successor_slug",
            )
            .first()
        )
        if product is None:
            raise HttpError(404, "product not found")

        if product.successor_product_uid and product.successor_slug:
            response.status_code = 301
            response["Location"] = _location(product.successor_slug)
            return 301, _redirect_target(product, product.slug)

        return 200, _product_response(product)


@api.get(
    "/v1/sellers/{seller_key}",
    response=SellerOut,
    tags=["sellers"],
    summary="Seller profile and trust breakdown",
)
def get_seller(request: HttpRequest, seller_key: str) -> Any:
    with API_LATENCY.labels(endpoint="get_seller").time():
        seller = Seller.objects.filter(seller_key=seller_key).first()
        if seller is None:
            raise HttpError(404, "seller not found")

        product_count = (
            ProductOffer.objects.filter(seller_key=seller_key, is_active=True)
            .values("product_id")
            .distinct()
            .count()
        )

        return {
            "seller_key": seller.seller_key,
            "name": seller.name,
            "domain": seller.domain,
            "source_key": seller.source_key,
            "is_panel": seller.is_panel,
            "tier": seller.effective_tier,
            "tier_override": seller.tier_override,
            "trust_score": seller.trust_score,
            "price_accuracy": seller.price_accuracy,
            "stock_accuracy": seller.stock_accuracy,
            "price_observations": seller.price_observations,
            "price_hits": seller.price_hits,
            "stock_observations": seller.stock_observations,
            "stock_hits": seller.stock_hits,
            "domain_age_days": seller.domain_age_days,
            "contact_completeness": seller.contact_completeness,
            "has_trust_badge": seller.has_trust_badge,
            "is_new_seller": seller.is_new_seller,
            "product_count": product_count,
            "updated_at": iso_utc(seller.updated_at),
        }


@api.get("/v1/health", response=HealthOut, tags=["ops"], summary="Liveness and DB reachability")
def health(request: HttpRequest) -> HealthOut:
    database = "ok"
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        database = "unavailable"
    return HealthOut(
        status="ok" if database == "ok" else "degraded", service="catalog", database=database
    )


def metrics_view(request: HttpRequest) -> HttpResponse:
    """Prometheus exposition. Deliberately a plain Django view: this is a
    text format, not part of the JSON API contract."""
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
