"""Ordering the seller list.

**Trust score first**, then price, then stock, then price freshness.

That ordering is taken literally, and it has to be: a plain weighted sum of
all four cannot express it. Give price any weight large enough to matter and
a big enough discount will eventually outvote any trust gap — which is
precisely the outcome "trust first" exists to prevent, and which contradicts
the published example payloads, where the seller list is in strict trust
order even when a cheaper seller sits below.

So trust is the primary key, compared in **bands**: two sellers whose scores
differ by less than ``RANKING_TRUST_BAND`` are treated as equally trustworthy,
because the score is an estimate and pretending it resolves to three decimal
places would be false precision. Price, stock and freshness then decide
within a band, by the weighted formula whose weights live in settings.

**CPC is not an input and never will be.** Sellers pay per outbound click,
and the moment payment could move a row up this list the comparison stops
being a comparison. Neutrality is the product's core asset. There is a test
that asserts click data does not reach this module.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

from django.conf import settings

from catalog.models import StockStatus

#: How much each stock state is worth to the ranking. "unknown" sits between
#: the two: it is not a promise, but it is not a refusal either.
STOCK_SCORE: dict[str, float] = {
    StockStatus.IN_STOCK.value: 1.0,
    StockStatus.UNKNOWN.value: 0.5,
    StockStatus.OUT_OF_STOCK.value: 0.0,
}


@dataclass(frozen=True)
class RankableOffer:
    offer_uid: str
    seller_key: str
    trust_score: float
    price_toman: int | None
    stock_status: str
    last_seen_at: dt.datetime | None


@dataclass(frozen=True)
class RankedOffer:
    offer_uid: str
    rank_position: int
    rank_score: float
    components: dict[str, float]
    #: Which trust band this offer landed in. Equal bands are what let price
    #: decide; unequal bands are what stop it from deciding.
    trust_band: int = 0


def price_score(price: int | None, cheapest: int | None, dearest: int | None) -> float:
    """1.0 for the cheapest offer, 0.0 for the dearest, linear between.

    An offer with no usable price scores zero rather than being dropped: it
    still belongs on the page, it just cannot compete on a number it does
    not have.
    """
    if price is None or cheapest is None or dearest is None:
        return 0.0
    if dearest == cheapest:
        return 1.0
    return (dearest - price) / (dearest - cheapest)


def freshness_score(last_seen_at: dt.datetime | None, now: dt.datetime) -> float:
    """Exponential decay on how recently we saw this price.

    A stale price is a small penalty, not a disqualification — it halves
    every ``PRICE_FRESHNESS_HALFLIFE_DAYS``.
    """
    if last_seen_at is None:
        return 0.0
    age_days = max(0.0, (now - last_seen_at).total_seconds() / 86400.0)
    halflife = settings.PRICE_FRESHNESS_HALFLIFE_DAYS or 1.0
    return math.pow(0.5, age_days / halflife)


def trust_band(trust_score: float, band: float | None = None) -> int:
    """Which band of comparable trustworthiness a score falls into.

    Banding keeps the ordering honest in both directions: a materially more
    trustworthy seller always leads, and two sellers who are effectively
    level are settled on price rather than on the third decimal place of an
    estimate.
    """
    width = band or settings.RANKING_TRUST_BAND
    if width <= 0:
        return 0
    # The epsilon is not decoration: 0.60 / 0.05 is 11.999999999999998 in
    # binary floating point, so a bare floor would drop a score sitting
    # exactly on a boundary into the band below and reverse two sellers who
    # are meant to be level.
    return math.floor(max(0.0, min(1.0, trust_score)) / width + 1e-9)


def rank(offers: Sequence[RankableOffer], now: dt.datetime) -> list[RankedOffer]:
    """Score and order the seller list. Position 1 is the top of the page."""
    if not offers:
        return []

    weights = settings.RANKING_WEIGHTS
    band = settings.RANKING_TRUST_BAND
    prices = [o.price_toman for o in offers if o.price_toman is not None]
    cheapest = min(prices) if prices else None
    dearest = max(prices) if prices else None
    secondary_weight = sum(weights[name] for name in ("price", "stock", "freshness")) or 1.0

    scored: list[tuple[int, float, str, dict[str, float]]] = []
    for offer in offers:
        components = {
            "trust": max(0.0, min(1.0, offer.trust_score)),
            "price": price_score(offer.price_toman, cheapest, dearest),
            "stock": STOCK_SCORE.get(offer.stock_status, 0.0),
            "freshness": freshness_score(offer.last_seen_at, now),
        }
        secondary = (
            sum(weights[name] * components[name] for name in ("price", "stock", "freshness"))
            / secondary_weight
        )
        scored.append(
            (trust_band(offer.trust_score, band), round(secondary, 6), offer.offer_uid, components)
        )

    # Trust band first, then the weighted secondary score; offer_uid breaks
    # the last tie so the order is identical on every rebuild of the same data.
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))

    return [
        RankedOffer(
            offer_uid=uid,
            rank_position=index,
            # A single monotone number whose ordering matches the sort, so a
            # stored rank_score can be reasoned about on its own.
            rank_score=round(bucket * band + secondary * band, 6),
            components=components,
            trust_band=bucket,
        )
        for index, (bucket, secondary, uid, components) in enumerate(scored, start=1)
    ]
