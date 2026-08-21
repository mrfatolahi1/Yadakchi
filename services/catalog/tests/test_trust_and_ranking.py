"""Acceptance criterion 7 — trust from observed accuracy, and new sellers
ranked below established ones at equal price.

Also the neutrality guarantee: nothing about clicks, cost or CPC may reach
the ranking. Paid placement is rejected, and that has to be enforced by a
test rather than by good intentions.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings

from catalog import ranking, trust
from catalog.models import OfferReadModel, Seller, SellerTier, StockStatus

NOW = dt.datetime(2026, 8, 19, 7, 0, 0, tzinfo=dt.UTC)
pytestmark = pytest.mark.django_db


def _seller(key: str, **kwargs: object) -> Seller:
    defaults: dict[str, object] = {
        "name": key,
        "domain": f"{key}.ir",
        "source_key": key,
        "first_seen_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(kwargs)
    return Seller.objects.create(seller_key=key, **defaults)


# ------------------------------------------------------------------- trust
def test_accuracy_is_smoothed_not_raw() -> None:
    """One lucky observation must not buy a perfect score."""
    prior, strength = 0.5, 5.0
    single = trust.smoothed_accuracy(1, 1, prior=prior, strength=strength)
    many = trust.smoothed_accuracy(100, 100, prior=prior, strength=strength)
    assert single < 0.6
    assert many > 0.95
    assert trust.smoothed_accuracy(0, 0, prior=prior, strength=strength) == prior


def test_price_and_stock_accuracy_dominate_the_score() -> None:
    """The two signals we own are 70% of the score between them, so a seller
    who lies about price cannot be rescued by being on the panel."""
    accurate = _seller(
        "accurate", price_hits=95, price_observations=100, stock_hits=90, stock_observations=100
    )
    liar = _seller(
        "liar",
        is_panel=True,
        price_hits=20,
        price_observations=100,
        stock_hits=15,
        stock_observations=100,
    )
    assert trust.compute_trust(accurate).trust_score > trust.compute_trust(liar).trust_score


def test_a_new_seller_is_capped_and_flagged() -> None:
    """Cold start: no history means tier `new`, a hard ceiling, and a badge.
    Visibility is earned — this is the only quality gate in the system."""
    newcomer = _seller("newcomer")
    result = trust.compute_trust(newcomer)

    assert result.tier == SellerTier.NEW
    assert result.trust_score <= settings.TRUST_TIER_CEILING["new"]
    # Not enough evidence to publish a number at all.
    assert result.price_accuracy is None
    assert result.stock_accuracy is None

    trust.apply_trust(newcomer, NOW)
    assert newcomer.is_new_seller


def test_an_established_seller_outranks_a_new_one_at_the_same_price() -> None:
    """Acceptance criterion 7, stated exactly."""
    established = _seller(
        "established",
        price_hits=98,
        price_observations=100,
        stock_hits=95,
        stock_observations=100,
    )
    newcomer = _seller("newcomer")
    for seller in (established, newcomer):
        trust.apply_trust(seller, NOW)
        seller.save()

    assert established.tier == SellerTier.TRUSTED
    assert newcomer.tier == SellerTier.NEW
    assert established.trust_score > newcomer.trust_score

    same_price = 2_450_000
    ranked = ranking.rank(
        [
            ranking.RankableOffer(
                "b" * 32, "newcomer", newcomer.trust_score, same_price, StockStatus.IN_STOCK, NOW
            ),
            ranking.RankableOffer(
                "a" * 32,
                "established",
                established.trust_score,
                same_price,
                StockStatus.IN_STOCK,
                NOW,
            ),
        ],
        NOW,
    )
    assert [r.offer_uid for r in ranked] == ["a" * 32, "b" * 32]
    assert ranked[0].rank_position == 1


def test_a_human_suspension_is_sticky() -> None:
    """Principle 4: a reprocess never erases a human decision."""
    seller = _seller(
        "shady",
        price_hits=99,
        price_observations=100,
        stock_hits=99,
        stock_observations=100,
        tier_override=SellerTier.SUSPENDED,
    )
    result = trust.compute_trust(seller)
    assert result.tier == SellerTier.SUSPENDED
    assert result.trust_score <= settings.TRUST_TIER_CEILING["suspended"]


def test_observations_come_from_our_own_crawl_history() -> None:
    """A price that held is a hit; a price that moved is a miss. Stock is
    only counted when the previous state actually claimed in_stock."""
    seller = _seller("observed")
    previous = OfferReadModel(
        offer_uid="a" * 32,
        seller_key="observed",
        price_toman=1000,
        stock_status=StockStatus.IN_STOCK,
    )

    trust.observe_offer_change(seller, previous, 1000, StockStatus.IN_STOCK)
    assert (seller.price_observations, seller.price_hits) == (1, 1)
    assert (seller.stock_observations, seller.stock_hits) == (1, 1)

    trust.observe_offer_change(seller, previous, 1200, StockStatus.OUT_OF_STOCK)
    assert (seller.price_observations, seller.price_hits) == (2, 1)
    assert (seller.stock_observations, seller.stock_hits) == (2, 1)

    # Previously out of stock: "was in stock still true?" has no meaning.
    previous.stock_status = StockStatus.OUT_OF_STOCK
    trust.observe_offer_change(seller, previous, 1200, StockStatus.IN_STOCK)
    assert seller.stock_observations == 2


# ----------------------------------------------------------------- ranking
def test_trust_beats_price() -> None:
    """Trust first is the whole point: the cheapest seller does not
    automatically lead the list."""
    ranked = ranking.rank(
        [
            ranking.RankableOffer(
                "c" * 32, "cheap_untrusted", 0.30, 1_000_000, StockStatus.IN_STOCK, NOW
            ),
            ranking.RankableOffer(
                "t" * 32, "dear_trusted", 0.85, 1_500_000, StockStatus.IN_STOCK, NOW
            ),
        ],
        NOW,
    )
    assert ranked[0].offer_uid == "t" * 32


def test_price_breaks_a_trust_tie() -> None:
    ranked = ranking.rank(
        [
            ranking.RankableOffer("d" * 32, "a", 0.60, 1_500_000, StockStatus.IN_STOCK, NOW),
            ranking.RankableOffer("e" * 32, "b", 0.60, 1_000_000, StockStatus.IN_STOCK, NOW),
        ],
        NOW,
    )
    assert ranked[0].offer_uid == "e" * 32


def test_stock_breaks_a_tie_after_price() -> None:
    ranked = ranking.rank(
        [
            ranking.RankableOffer("f" * 32, "a", 0.6, 1_000_000, StockStatus.OUT_OF_STOCK, NOW),
            ranking.RankableOffer("g" * 32, "b", 0.6, 1_000_000, StockStatus.IN_STOCK, NOW),
        ],
        NOW,
    )
    assert ranked[0].offer_uid == "g" * 32


def test_a_stale_price_loses_to_a_fresh_one() -> None:
    ranked = ranking.rank(
        [
            ranking.RankableOffer(
                "h" * 32, "a", 0.6, 1_000_000, StockStatus.IN_STOCK, NOW - dt.timedelta(days=60)
            ),
            ranking.RankableOffer("i" * 32, "b", 0.6, 1_000_000, StockStatus.IN_STOCK, NOW),
        ],
        NOW,
    )
    assert ranked[0].offer_uid == "i" * 32


def test_ranking_is_deterministic_for_identical_offers() -> None:
    offers = [
        ranking.RankableOffer("z" * 32, "a", 0.5, 1000, StockStatus.IN_STOCK, NOW),
        ranking.RankableOffer("y" * 32, "b", 0.5, 1000, StockStatus.IN_STOCK, NOW),
    ]
    assert [r.offer_uid for r in ranking.rank(offers, NOW)] == [
        r.offer_uid for r in ranking.rank(list(reversed(offers)), NOW)
    ]


def test_ranking_cannot_see_clicks_or_cost() -> None:
    """Neutrality is the core asset. `RankableOffer` is the only input to
    ranking, and it has no field a seller could pay into — no click count, no
    CPC, no bid, no cost. If someone adds one, this test fails."""
    fields = set(ranking.RankableOffer.__dataclass_fields__)
    assert fields == {
        "offer_uid",
        "seller_key",
        "trust_score",
        "price_toman",
        "stock_status",
        "last_seen_at",
    }
    forbidden = {"cpc", "cost", "click", "bid", "paid", "sponsor", "promoted"}
    source = (ranking.__file__ or "").replace(".pyc", ".py")
    body = open(source, encoding="utf-8").read().lower()  # noqa: PTH123, SIM115
    code_only = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))
    # The docstring names them to explain the ban; no executable line may.
    code_only = code_only.split('"""')[0] + '"""'.join(code_only.split('"""')[2:])
    assert not any(token in code_only for token in forbidden)


def test_a_large_discount_cannot_outvote_a_real_trust_gap() -> None:
    """The failure mode a flat weighted sum has: a third of the price buys
    the top slot. Trust bands are why it does not."""
    ranked = ranking.rank(
        [
            ranking.RankableOffer("c" * 32, "cheap", 0.45, 1_000_000, StockStatus.IN_STOCK, NOW),
            ranking.RankableOffer("t" * 32, "trusted", 0.77, 3_000_000, StockStatus.IN_STOCK, NOW),
        ],
        NOW,
    )
    assert ranked[0].offer_uid == "t" * 32


def test_sellers_within_a_band_are_settled_on_price() -> None:
    """Two scores this close are the same estimate. Price decides, which is
    what the user came for."""
    band = settings.RANKING_TRUST_BAND
    ranked = ranking.rank(
        [
            ranking.RankableOffer("x" * 32, "a", 0.60, 2_000_000, StockStatus.IN_STOCK, NOW),
            ranking.RankableOffer(
                "w" * 32, "b", 0.60 + band / 4, 1_000_000, StockStatus.IN_STOCK, NOW
            ),
        ],
        NOW,
    )
    assert ranked[0].offer_uid == "w" * 32
    assert ranked[0].trust_band == ranked[1].trust_band
