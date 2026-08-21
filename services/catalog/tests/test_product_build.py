"""Acceptance criteria 3, 4, 5, 8 and 11 — building a presentable product.

These run the real consumer handlers, so what is asserted is what the service
would actually do with those messages off the topic.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from catalog.models import (
    AuthenticityClaim,
    OfferReadModel,
    Product,
    ProductOffer,
    Seller,
    StockStatus,
)
from catalog.rebuild import rebuild_product
from tests.conftest import (
    NOW,
    Pipeline,
    SellerFactory,
    cluster_payload,
    fitment_payload,
    offer_payload,
    offer_uid,
)

pytestmark = pytest.mark.django_db

CLUSTER = uuid.UUID("93c9da93-7ffb-498e-afc1-2798ea05112e")


def build_cluster(
    pipeline: Pipeline,
    seeds: list[str],
    *,
    cluster_uid: uuid.UUID = CLUSTER,
    **offer_overrides: dict[str, Any],
) -> Product:
    """Feed a cluster and its member offers, in the order a broker would."""
    for seed in seeds:
        overrides = offer_overrides.get(seed, {})
        pipeline.feed("offers.enriched", offer_payload(seed, **overrides))
    pipeline.feed("clusters.changed", cluster_payload(cluster_uid, seeds))
    for seed in seeds:
        pipeline.feed("offers.fitted", fitment_payload(seed))
    return Product.objects.get(product_uid=cluster_uid)


# ============ criterion 4: min_price ignores out-of-stock, which still shows
def test_min_price_uses_in_stock_offers_only(pipeline: Pipeline, seeded_vehicles: None) -> None:
    """The headline lowest price must never be a number nobody can buy.

    The cheapest offer here is out of stock at ۲۳۸۰۰۰۰. The headline must be
    the cheapest *buyable* price, ۲۴۵۰۰۰۰ — and the out-of-stock row must
    still be on the page, labelled.
    """
    product = build_cluster(
        pipeline,
        ["a", "b", "c"],
        a={"seller_key": "yadakyar", "price_toman": 2_450_000, "stock_status": "in_stock"},
        b={"seller_key": "yadaksara", "price_toman": 2_380_000, "stock_status": "out_of_stock"},
        c={"seller_key": "otoyar", "price_toman": 2_690_000, "stock_status": "in_stock"},
    )

    assert product.min_price_toman == 2_450_000
    assert product.max_price_toman == 2_690_000
    assert product.median_price_toman == 2_570_000

    # The out-of-stock offer is retained and labelled, never deleted.
    rows = {o["offer_uid"]: o for o in product.document["offers"]}
    out_of_stock = rows[offer_uid("b")]
    assert out_of_stock["stock_status"] == StockStatus.OUT_OF_STOCK
    assert out_of_stock["price_toman"] == 2_380_000
    assert product.offer_count == 3


def test_min_price_is_null_when_nothing_is_in_stock(
    pipeline: Pipeline, seeded_vehicles: None
) -> None:
    product = build_cluster(
        pipeline,
        ["a", "b"],
        a={"stock_status": "out_of_stock"},
        b={"seller_key": "otoyar", "stock_status": "out_of_stock", "price_toman": 999_000},
    )
    assert product.min_price_toman is None
    assert product.median_price_toman is None
    assert product.offer_count == 2  # still shown, still labelled


# ================================ criterion 8: exactly one offer is cheapest
def test_exactly_one_offer_is_marked_cheapest(pipeline: Pipeline, seeded_vehicles: None) -> None:
    product = build_cluster(
        pipeline,
        ["a", "b", "c"],
        a={"seller_key": "yadakyar", "price_toman": 2_450_000},
        b={"seller_key": "yadaksara", "price_toman": 2_380_000},
        c={"seller_key": "otoyar", "price_toman": 2_690_000},
    )
    flags = [o["is_cheapest"] for o in product.document["offers"]]
    assert flags.count(True) == 1
    cheapest = next(o for o in product.document["offers"] if o["is_cheapest"])
    assert cheapest["price_toman"] == product.min_price_toman

    assert ProductOffer.objects.filter(product=product, is_cheapest=True).count() == 1


def test_the_cheapest_badge_can_land_below_the_top_of_the_list(
    pipeline: Pipeline, seeded_vehicles: None, seller_factory: SellerFactory
) -> None:
    """Trust-first ordering puts a dearer seller first. Users came for price,
    so the cheapest still has to be badged wherever it ends up."""
    seller_factory(
        "trusted_co", price_hits=98, price_observations=100, stock_hits=97, stock_observations=100
    )
    seller_factory("cheap_co")

    product = build_cluster(
        pipeline,
        ["a", "b"],
        a={"seller_key": "trusted_co", "price_toman": 3_000_000},
        b={"seller_key": "cheap_co", "price_toman": 1_000_000},
    )
    offers = product.document["offers"]
    assert offers[0]["seller_key"] == "trusted_co"  # trust first
    assert offers[0]["is_cheapest"] is False
    assert offers[1]["seller_key"] == "cheap_co"
    assert offers[1]["is_cheapest"] is True  # ...but badged


def test_one_offer_is_cheapest_even_with_no_usable_price(
    pipeline: Pipeline, seeded_vehicles: None
) -> None:
    """`price_toman` is nullable: enricher never guesses an ambiguous price.
    The badge still lands somewhere so the frontend has one rule, not two."""
    product = build_cluster(pipeline, ["a"], a={"price_toman": None})
    assert [o["is_cheapest"] for o in product.document["offers"]] == [True]
    assert product.min_price_toman is None


# ========== criterion 5: a mostly-genuine cluster keeps a genuine identity
def test_a_mostly_genuine_cluster_does_not_pick_an_aftermarket_representative(
    pipeline: Pipeline, seeded_vehicles: None, seller_factory: SellerFactory
) -> None:
    """Aggressive merging puts copies in genuine clusters. The aftermarket
    listing here is the best-scored candidate on every other axis — richer
    fields, better seller, an image — and it must still lose, because the
    cluster is genuine and the title must not mislabel the product."""
    seller_factory(
        "premium", price_hits=99, price_observations=100, stock_hits=99, stock_observations=100
    )
    seller_factory("modest")

    product = build_cluster(
        pipeline,
        ["g1", "g2", "after"],
        g1={
            "seller_key": "modest",
            "authenticity_claim": "genuine",
            "title_normalized": "لنت ترمز جلو پژو 206 عظام",
        },
        g2={
            "seller_key": "modest",
            "authenticity_claim": "genuine",
            "title_normalized": "لنت ترمز جلو پژو 206 تیپ 5 عظام",
        },
        after={
            "seller_key": "premium",
            "authenticity_claim": "aftermarket",
            "title_normalized": "لنت ترمز جلو پژو 206 چینی درجه یک",
            "brand": "generic",
            "part_number": "999999",
            "image_url": "https://cdn.example.com/pretty.jpg",
        },
    )

    assert product.authenticity_dominant == AuthenticityClaim.GENUINE
    assert product.representative_offer_uid != offer_uid("after")
    assert "چینی" not in product.title
    assert product.representative_reason["gate"] == "authenticity_dominant"


def test_dominant_claim_ignores_unknown_when_anything_was_claimed(
    pipeline: Pipeline, seeded_vehicles: None
) -> None:
    """An "unknown" is an absence of information; it must not outvote real
    claims just by being common."""
    product = build_cluster(
        pipeline,
        ["u1", "u2", "g1"],
        u1={"authenticity_claim": "unknown"},
        u2={"authenticity_claim": "unknown", "seller_key": "otoyar"},
        g1={"authenticity_claim": "genuine", "seller_key": "yadaksara"},
    )
    assert product.authenticity_dominant == AuthenticityClaim.GENUINE


# ============ criterion 3: deterministic re-election, never an empty title
def test_removing_the_representative_re_elects_deterministically(
    pipeline: Pipeline, seeded_vehicles: None
) -> None:
    product = build_cluster(pipeline, ["a", "b", "c"])
    original = product.representative_offer_uid
    assert original is not None
    assert product.title

    # The representative's listing disappears from the source.
    gone = next(seed for seed in ("a", "b", "c") if offer_uid(seed) == original)
    pipeline.feed(
        "offers.enriched",
        offer_payload(gone, is_active=False),
        occurred_at=NOW + dt.timedelta(minutes=1),
    )

    product.refresh_from_db()
    assert product.representative_offer_uid != original
    assert product.title, "a product must never be left without a title"
    assert product.offer_count == 2

    # Deterministic: rebuilding lands on the same replacement, every time.
    elected = product.representative_offer_uid
    rebuild_product(product.product_uid, NOW + dt.timedelta(minutes=2))
    product.refresh_from_db()
    assert product.representative_offer_uid == elected


def test_the_title_is_never_empty_even_when_every_title_is_junk(
    pipeline: Pipeline, seeded_vehicles: None
) -> None:
    """Every member's normalized title is pure marketing. The product still
    needs a title: an empty <title> is a broken page and the contract
    forbids it."""
    product = build_cluster(
        pipeline,
        ["j1"],
        j1={"title_normalized": "ارسال رایگان", "raw_title": "ارسال رایگان"},
    )
    assert product.title.strip()
    assert product.document["title"].strip()


def test_the_title_comes_from_title_normalized_not_raw_title(
    pipeline: Pipeline, seeded_vehicles: None
) -> None:
    """The single most expensive regression in this service."""
    product = build_cluster(pipeline, ["a"])
    offer = OfferReadModel.objects.get(offer_uid=offer_uid("a"))
    assert "ارسال رایگان" in offer.raw_title
    assert product.title == offer.title_normalized
    assert "ارسال رایگان" not in product.title


# ================================ criterion 11: a thin product is not published
def test_a_thin_product_fails_the_publication_gate(
    pipeline: Pipeline, seeded_vehicles: None
) -> None:
    """One offer, no usable price, no part number, no cross-reference and no
    fitment verdict. Index-everything means a page like this costs the whole
    domain, so it stays unpublished."""
    for seed in ["thin"]:
        pipeline.feed(
            "offers.enriched",
            offer_payload(
                seed,
                price_toman=None,
                part_number=None,
                brand=None,
                title_normalized="چراغ جلو راست پژو 206",
            ),
        )
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["thin"]))
    pipeline.feed(
        "offers.fitted",
        fitment_payload(
            "thin",
            fitments=[
                {
                    "vehicle_slug": "peugeot-206-type-5",
                    "status": "unknown",
                    "confidence": 0.2,
                    "provenance": "model",
                    "evidence": {},
                }
            ],
            crossref_codes=[],
            risky_family={
                "part_type": "headlight_right",
                "required_granularity": "year",
                "note_fa": "چراغ جلو پژو ۲۰۶ بین سال‌های تولید متفاوت است.",
            },
        ),
    )

    product = Product.objects.get(product_uid=CLUSTER)
    assert product.is_published is False
    assert "thin_content" in product.unpublished_reasons
    assert "no_usable_price" in product.unpublished_reasons
    # The warning is carried even though the page is not published.
    assert product.risky_family_note_fa
    assert product.document["vehicles_unknown"] == ["peugeot-206-type-5"]


def test_a_substantial_single_offer_product_is_published(
    pipeline: Pipeline, seeded_vehicles: None
) -> None:
    """A single-offer product is fine, as long as it carries real content."""
    product = build_cluster(pipeline, ["a"])
    assert product.is_published is True
    assert product.unpublished_reasons == []
    assert product.part_numbers == ["425438"]
    assert product.vehicles_compatible == ["peugeot-206-type-5"]
    assert "425235" in product.crossref_codes


def test_a_product_with_no_active_offers_is_unpublished(
    pipeline: Pipeline, seeded_vehicles: None
) -> None:
    build_cluster(pipeline, ["a"])
    pipeline.feed(
        "offers.enriched",
        offer_payload("a", is_active=False),
        occurred_at=NOW + dt.timedelta(minutes=1),
    )
    product = Product.objects.get(product_uid=CLUSTER)
    assert product.is_published is False
    assert "no_active_offers" in product.unpublished_reasons
    assert product.offer_count == 0
    assert product.document["offers"] == []


# ================================================== seller identity is ours
def test_a_seller_is_provisioned_from_the_first_offer(pipeline: Pipeline) -> None:
    """No topic carries a seller's name, domain or panel membership, so
    catalog mints the record itself and a human corrects it later."""
    pipeline.feed("offers.enriched", offer_payload("a", seller_key="newshop"))
    seller = Seller.objects.get(seller_key="newshop")
    assert seller.name == "newshop"
    assert seller.domain == "yadakmarket.com"
    assert seller.tier == "new"
    assert seller.is_panel is False
