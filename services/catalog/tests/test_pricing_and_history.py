"""Price statistics and the downsampled series `web` draws its chart from."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from catalog import pricing
from catalog.models import PriceHistory, Product, StockStatus
from tests.conftest import (
    NOW,
    Pipeline,
    cluster_payload,
    fitment_payload,
    offer_payload,
    offer_uid,
)

pytestmark = pytest.mark.django_db
CLUSTER = uuid.UUID("93c9da93-7ffb-498e-afc1-2798ea05112e")


def point(offer: str, day: str, price: int | None, stock: str = "in_stock") -> pricing.HistoryPoint:
    return pricing.HistoryPoint(
        offer, dt.datetime.fromisoformat(f"{day}T09:00:00+00:00"), price, stock
    )


# ------------------------------------------------------------------ medians
def test_median_of_an_even_count_rounds_half_up() -> None:
    """Bankers' rounding would make the value depend on parity, and the
    contract wants an integer toman that is the same on every rebuild."""
    assert pricing.median_int([2_450_000, 2_690_000]) == 2_570_000
    assert pricing.median_int([295_000, 320_000]) == 307_500
    assert pricing.median_int([1, 2]) == 2
    assert pricing.median_int([1, 2, 3]) == 2


def test_statistics_come_from_in_stock_offers_only() -> None:
    stats = pricing.price_statistics(
        [
            pricing.PricedOffer("a" * 32, 2_450_000, StockStatus.IN_STOCK),
            pricing.PricedOffer("b" * 32, 100, StockStatus.OUT_OF_STOCK),
            pricing.PricedOffer("c" * 32, 2_690_000, StockStatus.IN_STOCK),
            pricing.PricedOffer("d" * 32, None, StockStatus.IN_STOCK),
        ]
    )
    assert (stats.min_toman, stats.max_toman, stats.median_toman) == (
        2_450_000,
        2_690_000,
        2_570_000,
    )


def test_unknown_stock_does_not_vote_on_the_headline_price() -> None:
    """ "Absence maps to unknown, never in_stock" upstream — so unknown must
    not become a buyable claim down here either."""
    stats = pricing.price_statistics([pricing.PricedOffer("a" * 32, 500, StockStatus.UNKNOWN)])
    assert stats.min_toman is None


# ------------------------------------------------------------------ series
def test_the_series_carries_a_price_forward_between_observations() -> None:
    """History is recorded on change, so a day with no observation is not a
    day with no price."""
    series = pricing.daily_series(
        [point("a" * 32, "2026-08-17", 300_000)],
        start=dt.date(2026, 8, 17),
        end=dt.date(2026, 8, 19),
    )
    assert [p["date"] for p in series] == ["2026-08-17", "2026-08-18", "2026-08-19"]
    assert {p["min_toman"] for p in series} == {300_000}


def test_observations_before_the_window_seed_it_without_appearing() -> None:
    series = pricing.daily_series(
        [point("a" * 32, "2026-01-01", 300_000)],
        start=dt.date(2026, 8, 18),
        end=dt.date(2026, 8, 19),
    )
    assert [p["date"] for p in series] == ["2026-08-18", "2026-08-19"]


def test_a_day_with_nothing_in_stock_produces_no_point() -> None:
    """A gap in the chart is honest; an invented price is not."""
    series = pricing.daily_series(
        [
            point("a" * 32, "2026-08-17", 300_000),
            point("a" * 32, "2026-08-18", 300_000, stock="out_of_stock"),
        ],
        start=dt.date(2026, 8, 17),
        end=dt.date(2026, 8, 18),
    )
    assert [p["date"] for p in series] == ["2026-08-17"]


def test_the_series_summarises_across_offers() -> None:
    series = pricing.daily_series(
        [
            point("a" * 32, "2026-08-17", 300_000),
            point("b" * 32, "2026-08-17", 400_000),
        ],
        start=dt.date(2026, 8, 17),
        end=dt.date(2026, 8, 17),
    )
    assert series == [{"date": "2026-08-17", "min_toman": 300_000, "median_toman": 350_000}]


# ------------------------------------------------------- end to end, stored
def test_the_product_payload_carries_the_chart(pipeline: Pipeline, seeded_vehicles: None) -> None:
    """`web` must not need a second call to draw the price history."""
    pipeline.feed(
        "offers.enriched",
        offer_payload("a", price_toman=2_400_000, last_seen_at="2026-08-17T09:00:00Z"),
        occurred_at=NOW - dt.timedelta(days=2),
    )
    pipeline.feed("clusters.changed", cluster_payload(CLUSTER, ["a"]))
    pipeline.feed("offers.fitted", fitment_payload("a"))
    pipeline.feed(
        "offers.enriched",
        offer_payload("a", price_toman=2_450_000, last_seen_at="2026-08-19T09:00:00Z"),
        occurred_at=NOW,
    )

    product = Product.objects.get(product_uid=CLUSTER)
    series = product.document["price_series"]
    assert series
    assert series[0]["date"] <= "2026-08-17"
    assert series[-1]["min_toman"] == 2_450_000
    assert PriceHistory.objects.filter(offer_uid=offer_uid("a")).count() == 2


def test_price_history_lands_in_a_monthly_partition(pipeline: Pipeline) -> None:
    """The table is range-partitioned; rows must reach a real partition, not
    only the catch-all."""
    from django.db import connection

    pipeline.feed("offers.enriched", offer_payload("a"))
    with connection.cursor() as cursor:
        cursor.execute("SELECT tableoid::regclass::text FROM catalog_pricehistory LIMIT 1")
        (partition,) = cursor.fetchone()
    assert partition.startswith("catalog_pricehistory_2026_08")
