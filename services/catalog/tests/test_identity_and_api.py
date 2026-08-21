"""Acceptance criteria 1 and 6 — URLs that keep working, pages that stay cheap.

Criterion 1 is asserted with a query counter, not by eye: a 20-offer product
must cost a small bounded number of queries, and it must not grow with the
number of offers.

Criterion 6 is the successor pointer. Splits are frequent with aggressive
merging upstream, and every unresolved old URL is a ranking that decays.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from urllib.parse import quote

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from catalog.models import Product, ProductSlug
from catalog.slugs import build_slug
from tests.conftest import (
    NOW,
    Pipeline,
    cluster_payload,
    fitment_payload,
    offer_payload,
)

pytestmark = pytest.mark.django_db

CLUSTER = uuid.UUID("93c9da93-7ffb-498e-afc1-2798ea05112e")
SUCCESSOR = uuid.UUID("b4191885-f836-4ccd-bfc4-e0ccaf88dcae")

#: A generous ceiling that still fails loudly if the ranked list is ever
#: fetched row by row. The point is that it does not scale with offer count.
MAX_PRODUCT_PAGE_QUERIES = 4


@pytest.fixture
def client() -> Client:
    return Client()


def build_20_offer_product(pipeline: Pipeline) -> Product:
    seeds = [f"offer-{index:02d}" for index in range(20)]
    for index, seed in enumerate(seeds):
        pipeline.feed(
            "offers.enriched",
            offer_payload(
                seed,
                seller_key=f"seller{index:02d}",
                price_toman=2_000_000 + index * 10_000,
            ),
        )
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, seeds))
    for seed in seeds:
        pipeline.feed("offers.fitted", fitment_payload(seed))
    return Product.objects.get(product_uid=CLUSTER)


# ============================== criterion 1: a bounded number of queries
def test_a_twenty_offer_product_page_costs_a_bounded_number_of_queries(
    pipeline: Pipeline, seeded_vehicles: None, client: Client
) -> None:
    """Acceptance criterion 1, asserted with a query counter."""
    product = build_20_offer_product(pipeline)
    assert product.offer_count == 20

    with CaptureQueriesContext(connection) as captured:
        response = client.get(f"/v1/products/{product.slug}")

    assert response.status_code == 200
    assert len(captured) <= MAX_PRODUCT_PAGE_QUERIES, [q["sql"] for q in captured]

    body = response.json()
    assert len(body["offers"]) == 20
    assert [o["rank_position"] for o in body["offers"]] == list(range(1, 21))


def test_query_count_does_not_grow_with_offer_count(
    pipeline: Pipeline, seeded_vehicles: None, client: Client
) -> None:
    """The property that actually matters: the page is one row read, so
    twenty offers cost exactly what one offer costs."""
    small = uuid.UUID("11111111-1111-4111-8111-111111111111")
    pipeline.feed("offers.enriched", offer_payload("solo"))
    pipeline.feed("clusters.changed", cluster_payload(small, ["solo"]))
    one = Product.objects.get(product_uid=small)

    with CaptureQueriesContext(connection) as small_queries:
        client.get(f"/v1/products/{one.slug}")

    twenty = build_20_offer_product(pipeline)
    with CaptureQueriesContext(connection) as big_queries:
        client.get(f"/v1/products/{twenty.slug}")

    assert len(big_queries) == len(small_queries)


def test_batch_hydration_is_one_query(
    pipeline: Pipeline, seeded_vehicles: None, client: Client
) -> None:
    product = build_20_offer_product(pipeline)
    with CaptureQueriesContext(connection) as captured:
        response = client.post(
            "/v1/products/batch",
            data=json.dumps({"product_uids": [str(product.product_uid), str(uuid.uuid4())]}),
            content_type="application/json",
        )
    assert response.status_code == 200
    assert len(captured) <= 2
    body = response.json()
    assert len(body["products"]) == 1
    assert len(body["missing"]) == 1


# ================== criterion 6: a split sets the successor and redirects
def test_a_split_sets_the_successor_and_the_old_slug_redirects(
    pipeline: Pipeline, seeded_vehicles: None, client: Client
) -> None:
    """Acceptance criterion 6.

    The original cluster is split: its members move to a new cluster, and
    matcher retires the old one with a successor. The old URL must answer a
    301 pointing at the replacement, not a 404.
    """
    for seed in ("a", "b"):
        pipeline.feed("offers.enriched", offer_payload(seed))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a", "b"]))
    for seed in ("a", "b"):
        pipeline.feed("offers.fitted", fitment_payload(seed))

    original = Product.objects.get(product_uid=CLUSTER)
    old_slug = original.slug

    # The split: a new cluster takes both members and names its predecessor.
    pipeline.feed(
        "clusters.changed",
        cluster_payload(
            SUCCESSOR, ["a", "b"], change_reason="split", predecessor_uids=[str(CLUSTER)]
        ),
        occurred_at=NOW + dt.timedelta(minutes=1),
    )
    # ...and the old cluster is retired, pointing at the replacement.
    pipeline.feed(
        "clusters.changed",
        cluster_payload(CLUSTER, [], change_reason="split", successor_uid=str(SUCCESSOR)),
        occurred_at=NOW + dt.timedelta(minutes=2),
    )

    retired = Product.objects.get(product_uid=CLUSTER)
    successor = Product.objects.get(product_uid=SUCCESSOR)

    assert retired.successor_product_uid == SUCCESSOR
    assert retired.is_published is False
    assert retired.document["successor_product_uid"] == str(SUCCESSOR)

    response = client.get(f"/v1/products/{old_slug}")
    assert response.status_code == 301
    assert response["Location"] == f"/v1/products/{quote(successor.slug, safe='')}"
    body = response.json()
    assert body["status"] == "retired"
    assert body["successor_product_uid"] == str(SUCCESSOR)


def test_a_product_is_never_deleted(pipeline: Pipeline, seeded_vehicles: None) -> None:
    """Retirement empties a product, it does not remove it. The row, the
    slug and the price history all survive."""
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))
    pipeline.feed("offers.fitted", fitment_payload("a"))
    before = Product.objects.get(product_uid=CLUSTER)
    assert before.price_series

    pipeline.feed(
        "clusters.changed",
        cluster_payload(SUCCESSOR, ["a"], predecessor_uids=[str(CLUSTER)]),
        occurred_at=NOW + dt.timedelta(minutes=1),
    )
    pipeline.feed(
        "clusters.changed",
        cluster_payload(CLUSTER, [], successor_uid=str(SUCCESSOR)),
        occurred_at=NOW + dt.timedelta(minutes=2),
    )

    retired = Product.objects.get(product_uid=CLUSTER)
    assert retired.offer_count == 0
    assert retired.document["offers"] == []
    # The chart it earned is kept: the history is still true.
    assert retired.price_series == before.price_series
    assert ProductSlug.objects.filter(product=retired).exists()


def test_an_old_slug_still_resolves_after_the_title_improves(
    pipeline: Pipeline, seeded_vehicles: None, client: Client
) -> None:
    """The slug is derived from the title and may change. Every slug the
    product has ever had keeps working."""
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))
    original_slug = Product.objects.get(product_uid=CLUSTER).slug

    pipeline.feed(
        "offers.enriched",
        offer_payload("a", title_normalized="لنت ترمز جلو پژو 206 تیپ 5 عظام اصلی"),
        occurred_at=NOW + dt.timedelta(minutes=1),
    )
    product = Product.objects.get(product_uid=CLUSTER)
    assert product.slug != original_slug

    response = client.get(f"/v1/products/{original_slug}")
    assert response.status_code == 301
    assert response.json()["status"] == "renamed"
    assert response["Location"] == f"/v1/products/{quote(product.slug, safe='')}"

    assert client.get(f"/v1/products/{product.slug}").status_code == 200


def test_the_slug_suffix_is_deterministic() -> None:
    """A rebuild must not reshuffle URLs."""
    assert build_slug("لنت ترمز", CLUSTER) == build_slug("لنت ترمز", CLUSTER)
    assert build_slug("لنت ترمز", CLUSTER) != build_slug("لنت ترمز", SUCCESSOR)


def test_the_slug_is_persian_and_url_safe(pipeline: Pipeline) -> None:
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))
    slug = Product.objects.get(product_uid=CLUSTER).slug
    assert slug.startswith("لنت-ترمز-جلو-پژو-206")
    assert " " not in slug


# ================================================================ other API
def test_lookup_by_uid(pipeline: Pipeline, seeded_vehicles: None, client: Client) -> None:
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))
    response = client.get(f"/v1/products/by-uid/{CLUSTER}")
    assert response.status_code == 200
    assert response.json()["product_uid"] == str(CLUSTER)


def test_unknown_slug_is_404(client: Client) -> None:
    assert client.get("/v1/products/nope-000000").status_code == 404


def test_seller_profile_for_ops(pipeline: Pipeline, client: Client) -> None:
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))

    response = client.get("/v1/sellers/yadakyar")
    assert response.status_code == 200
    body = response.json()
    assert body["seller_key"] == "yadakyar"
    assert body["tier"] == "new"
    assert body["is_new_seller"] is True
    assert body["product_count"] == 1


def test_health_and_metrics(client: Client) -> None:
    health = client.get("/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "catalog", "database": "ok"}

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert b"catalog_events_consumed_total" in metrics.content


def test_the_payload_carries_related_parts_for_a_thin_page(
    pipeline: Pipeline, seeded_vehicles: None, client: Client
) -> None:
    """Part six: even a single-offer product must carry related parts. They
    ride on the read API, because products.changed.v1 has no field for them."""
    other = uuid.UUID("22222222-2222-4222-8222-222222222222")
    pipeline.feed("offers.enriched", offer_payload("a"))
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))
    pipeline.feed("offers.fitted", fitment_payload("a"))

    pipeline.feed("offers.enriched", offer_payload("z", seller_key="otoyar"))
    pipeline.feed("clusters.changed", cluster_payload(other, ["z"]))
    pipeline.feed("offers.fitted", fitment_payload("z"))

    # Rebuild the first product now that the second one exists to relate to.
    from catalog.rebuild import rebuild_product

    rebuild_product(CLUSTER, NOW + dt.timedelta(minutes=1))

    body = client.get(f"/v1/products/{Product.objects.get(product_uid=CLUSTER).slug}").json()
    assert [r["product_uid"] for r in body["related_products"]] == [str(other)]
    assert body["seller_badges"]["yadakyar"]["is_new_seller"] is True
