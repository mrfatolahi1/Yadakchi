"""Price statistics and the downsampled history series.

One rule dominates this module: **the headline lowest price is computed from
in-stock offers only.** A "from ۲۹۵٬۰۰۰ تومان" that nobody can actually buy
is worse than no number at all — it is the single fastest way to lose a
user's trust. Out-of-stock offers are never hidden; they stay in the list,
labelled, and simply do not vote on the headline.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from catalog.models import StockStatus


@dataclass(frozen=True)
class PricedOffer:
    """The slice of an offer that pricing cares about."""

    offer_uid: str
    price_toman: int | None
    stock_status: str

    @property
    def is_in_stock(self) -> bool:
        return self.stock_status == StockStatus.IN_STOCK

    @property
    def counts_toward_headline(self) -> bool:
        return self.is_in_stock and self.price_toman is not None


@dataclass(frozen=True)
class PriceStatistics:
    min_toman: int | None
    max_toman: int | None
    median_toman: int | None


def median_int(values: Sequence[int]) -> int:
    """Median as an integer toman.

    Even-length input averages the two middle values and rounds half *up*,
    rather than Python's bankers' rounding, so the number is stable and
    reproducible across rebuilds.
    """
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return ordered[middle]
    low, high = ordered[middle - 1], ordered[middle]
    return (low + high + 1) // 2


def price_statistics(offers: Iterable[PricedOffer]) -> PriceStatistics:
    """min / max / median across **in-stock** offers only."""
    prices = [
        o.price_toman for o in offers if o.counts_toward_headline and o.price_toman is not None
    ]
    if not prices:
        return PriceStatistics(None, None, None)
    return PriceStatistics(min(prices), max(prices), median_int(prices))


def choose_cheapest(offers: Sequence[PricedOffer]) -> str | None:
    """Which offer wears the `is_cheapest` badge — exactly one, always.

    Users came for price. Trust-first ordering can push the cheapest seller
    down the list, so the frontend needs to badge it wherever it lands;
    hiding it would feel like bait-and-switch.

    Preference order, each step deterministic:

    1. the cheapest offer that is in stock and priced — the one that agrees
       with ``min_price_toman``;
    2. failing that, the cheapest priced offer whatever its stock state;
    3. failing that (nothing has a usable price at all), the first offer in
       the ranked list, so the badge still lands somewhere.

    Ties break on ``offer_uid`` so a rebuild never reshuffles the badge.
    """
    if not offers:
        return None

    in_stock = [o for o in offers if o.counts_toward_headline]
    if in_stock:
        return min(in_stock, key=lambda o: (o.price_toman or 0, o.offer_uid)).offer_uid

    priced = [o for o in offers if o.price_toman is not None]
    if priced:
        return min(priced, key=lambda o: (o.price_toman or 0, o.offer_uid)).offer_uid

    return offers[0].offer_uid


@dataclass(frozen=True)
class HistoryPoint:
    """One recorded observation of one offer."""

    offer_uid: str
    observed_at: dt.datetime
    price_toman: int | None
    stock_status: str


def daily_series(
    points: Iterable[HistoryPoint], *, start: dt.date, end: dt.date
) -> list[dict[str, int | str]]:
    """Downsample raw observations into one min/median point per day.

    History is recorded on *change*, so a day with no observation is not a
    day with no price: each offer's last known state is carried forward.
    Observations from before the window seed that state without emitting a
    point of their own. Days where nothing is in stock produce no point —
    a gap in the chart is honest, a fabricated price is not.
    """
    if end < start:
        return []

    ordered = sorted(points, key=lambda p: (p.observed_at, p.offer_uid))
    state: dict[str, tuple[int | None, str]] = {}
    cursor = 0

    # Seed from everything that happened before the window opened.
    while cursor < len(ordered) and ordered[cursor].observed_at.date() < start:
        point = ordered[cursor]
        state[point.offer_uid] = (point.price_toman, point.stock_status)
        cursor += 1

    series: list[dict[str, int | str]] = []
    day = start
    while day <= end:
        while cursor < len(ordered) and ordered[cursor].observed_at.date() <= day:
            point = ordered[cursor]
            state[point.offer_uid] = (point.price_toman, point.stock_status)
            cursor += 1

        prices = [
            price
            for price, stock in state.values()
            if price is not None and stock == StockStatus.IN_STOCK
        ]
        if prices:
            series.append(
                {
                    "date": day.isoformat(),
                    "min_toman": min(prices),
                    "median_toman": median_int(prices),
                }
            )
        day += dt.timedelta(days=1)

    return series
