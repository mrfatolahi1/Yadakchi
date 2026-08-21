"""Seller trust.

Trust-first ordering is meaningless on day one, because nobody has a history.
SPEC.md part four's answer is to build the score out of signals **we** own:

* **Price accuracy** — when we saw this listing again, did the advertised
  price still hold?
* **Stock accuracy** — when we saw it again, was "in stock" still true?

Those two carry 70% of the score between them, because a seller cannot fake
them. Everything else is either self-reported or structural, and is weighted
accordingly.

Two design notes that matter:

**Cold start.** A seller with no history does not get a neutral score and a
fair fight. They start in tier ``new`` with a hard ceiling and a badge, and
they climb by being observed. Onboarding is open and self-serve and
authenticity is unverified, so this ceiling is the only quality gate in the
system: visibility is earned.

**Smoothing.** One lucky observation must not buy a perfect score, so the
ratios are Beta-smoothed toward the neutral prior. Below
``TRUST_MIN_OBSERVATIONS`` we publish ``null`` accuracies — the contract has
a null for exactly this reason — while still ranking the seller from the
smoothed value.

**An assumption, stated plainly.** ``offers.enriched`` is emitted only when
something material changed for an offer; a re-observation with identical
values emits nothing. So the observations we can count are the ones we are
told about, and accuracy here measures *stability across the changes we
see* rather than a true crawl-by-crawl hit rate. That makes the number
comparative rather than absolute — which is all ranking needs, since every
seller is measured the same way.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings

from catalog.models import SellerTier, StockStatus

if TYPE_CHECKING:  # pragma: no cover
    from catalog.models import OfferReadModel, Seller


@dataclass(frozen=True)
class TrustResult:
    """What a recomputation decided, and why."""

    trust_score: float
    tier: str
    price_accuracy: float | None
    stock_accuracy: float | None
    components: dict[str, float]

    def differs_from(self, seller: Seller) -> bool:
        return (
            round(seller.trust_score, 6) != round(self.trust_score, 6)
            or seller.tier != self.tier
            or _rounded(seller.price_accuracy) != _rounded(self.price_accuracy)
            or _rounded(seller.stock_accuracy) != _rounded(self.stock_accuracy)
        )


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def smoothed_accuracy(hits: int, observations: int, *, prior: float, strength: float) -> float:
    """Beta-smoothed hit rate.

    With no observations this is exactly the prior; it approaches the raw
    ratio as evidence accumulates. Never 1.0 on a single lucky sample.
    """
    if observations <= 0:
        return prior
    return (hits + prior * strength) / (observations + strength)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _domain_age_score(days: int | None, prior: float) -> float:
    """Two years of domain age is a full mark; nothing known is the prior."""
    if days is None:
        return prior
    return _clamp01(days / 730.0)


def compute_trust(seller: Seller) -> TrustResult:
    """Score and tier a seller from what we have observed about them."""
    prior = settings.TRUST_NEUTRAL_PRIOR
    strength = settings.TRUST_SMOOTHING_STRENGTH
    weights = settings.TRUST_WEIGHTS

    price_smoothed = smoothed_accuracy(
        seller.price_hits, seller.price_observations, prior=prior, strength=strength
    )
    stock_smoothed = smoothed_accuracy(
        seller.stock_hits, seller.stock_observations, prior=prior, strength=strength
    )

    components = {
        "price_accuracy": price_smoothed,
        "stock_accuracy": stock_smoothed,
        "panel": 1.0 if seller.is_panel else 0.0,
        "domain_age": _domain_age_score(seller.domain_age_days, prior),
        "contact": prior
        if seller.contact_completeness is None
        else _clamp01(seller.contact_completeness),
        "badge": prior if seller.has_trust_badge is None else float(seller.has_trust_badge),
    }

    raw = sum(weights[name] * value for name, value in components.items())
    total_weight = sum(weights.values()) or 1.0
    raw = _clamp01(raw / total_weight)

    tier = decide_tier(seller, price_smoothed, stock_smoothed)
    ceiling = settings.TRUST_TIER_CEILING[tier]
    score = round(min(raw, ceiling), 4)

    # Published accuracies stay null until there is enough evidence to stand
    # behind them — the contract models this explicitly.
    min_obs = settings.TRUST_MIN_OBSERVATIONS
    public_price = round(price_smoothed, 4) if seller.price_observations >= min_obs else None
    public_stock = round(stock_smoothed, 4) if seller.stock_observations >= min_obs else None

    return TrustResult(
        trust_score=score,
        tier=tier,
        price_accuracy=public_price,
        stock_accuracy=public_stock,
        components={k: round(v, 4) for k, v in components.items()},
    )


def decide_tier(seller: Seller, price_accuracy: float, stock_accuracy: float) -> str:
    """Which tier a seller belongs in.

    A human decision wins outright and permanently — that is principle 4 of
    the brief, and it is also how suspension works: nothing computed here
    ever suspends a seller, and nothing computed here ever un-suspends one.
    """
    if seller.tier_override:
        return str(seller.tier_override)
    if seller.tier == SellerTier.SUSPENDED:
        return str(SellerTier.SUSPENDED)

    observations = min(seller.price_observations, seller.stock_observations)
    if observations < settings.TRUST_MIN_OBSERVATIONS:
        return str(SellerTier.NEW)
    if (
        observations >= settings.TRUST_TRUSTED_MIN_OBSERVATIONS
        and price_accuracy >= settings.TRUST_TRUSTED_THRESHOLD
        and stock_accuracy >= settings.TRUST_TRUSTED_THRESHOLD
    ):
        return str(SellerTier.TRUSTED)
    return str(SellerTier.STANDARD)


def observe_offer_change(
    seller: Seller, previous: OfferReadModel, incoming_price: int | None, incoming_stock: str
) -> tuple[bool, bool]:
    """Fold one re-observation of a known offer into the seller's counters.

    Returns ``(price_counted, stock_counted)`` so the caller can log what was
    recorded. Mutates the seller in memory; the caller saves.

    * A price observation counts whenever both the old and the new record
      carry a usable price. It is a *hit* when the price did not move: the
      advertised number still held.
    * A stock observation counts only when the previous state was
      ``in_stock`` — "was 'in stock' still true on recrawl" has no meaning
      otherwise — and is a hit when it still is.
    """
    price_counted = previous.price_toman is not None and incoming_price is not None
    if price_counted:
        seller.price_observations += 1
        if previous.price_toman == incoming_price:
            seller.price_hits += 1

    stock_counted = previous.stock_status == StockStatus.IN_STOCK
    if stock_counted:
        seller.stock_observations += 1
        if incoming_stock == StockStatus.IN_STOCK:
            seller.stock_hits += 1

    return price_counted, stock_counted


def apply_trust(seller: Seller, now: dt.datetime) -> bool:
    """Recompute and store. True when anything a consumer would notice moved."""
    result = compute_trust(seller)
    if not result.differs_from(seller):
        return False
    seller.trust_score = result.trust_score
    seller.tier = result.tier
    seller.price_accuracy = result.price_accuracy
    seller.stock_accuracy = result.stock_accuracy
    seller.updated_at = now
    return True
