"""Choosing the member offer that supplies the product's title and image.

With aggressive merging upstream, a cluster is a mixed bag: the member with
the fullest data may well be an aftermarket copy sitting in a cluster that is
otherwise genuine. Letting it name the product would mislabel the page, so
the dominant authenticity claim gates the candidate set before scoring
starts.

Every choice records why it was made. When the representative disappears —
the listing is delisted, the cluster splits — re-election has to be
deterministic and explicable, and the product must never be left without a
title.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.conf import settings

from catalog.models import AuthenticityClaim
from catalog.titles import is_sane_title, strip_promotional

if TYPE_CHECKING:  # pragma: no cover
    from catalog.models import OfferReadModel

#: Fields that make a listing "complete" enough to speak for the product.
COMPLETENESS_FIELDS = (
    "title_normalized",
    "brand",
    "part_number",
    "part_type",
    "image_url",
    "price_toman",
)

#: Deterministic order for breaking a tie in the modal authenticity claim.
#: A tie resolves toward the more specific claim, never toward "unknown".
_CLAIM_PRIORITY = (
    AuthenticityClaim.GENUINE,
    AuthenticityClaim.OEM,
    AuthenticityClaim.AFTERMARKET,
    AuthenticityClaim.REFURBISHED,
    AuthenticityClaim.USED,
    AuthenticityClaim.UNKNOWN,
)


@dataclass(frozen=True)
class Candidate:
    offer_uid: str
    score: float
    components: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Election:
    """The outcome, and enough provenance to explain and repeat it."""

    offer_uid: str | None
    title: str
    image_url: str | None
    authenticity_dominant: str
    reason: dict[str, object]


def dominant_authenticity(offers: Sequence[OfferReadModel]) -> str:
    """The modal authenticity claim across the cluster.

    "unknown" only wins when *nothing* else was claimed: an unknown is an
    absence of information, and it should not outvote real claims.
    """
    if not offers:
        return str(AuthenticityClaim.UNKNOWN)

    counts = Counter(o.authenticity_claim for o in offers)
    known = {claim: n for claim, n in counts.items() if claim != AuthenticityClaim.UNKNOWN}
    pool = known or dict(counts)
    best = max(pool.values())
    tied = [claim for claim, n in pool.items() if n == best]
    for claim in _CLAIM_PRIORITY:
        if claim in tied:
            return str(claim)
    return str(sorted(tied)[0])


def completeness(offer: OfferReadModel) -> float:
    filled = sum(1 for name in COMPLETENESS_FIELDS if getattr(offer, name, None))
    return filled / len(COMPLETENESS_FIELDS)


def _image_score(offer: OfferReadModel) -> float:
    """Present and not obviously watermarked.

    We cannot inspect pixels here, so this is what the URL will tell us:
    a marked-up filename is the one signal available without fetching bytes.
    """
    url = (offer.image_url or "").lower()
    if not url:
        return 0.0
    if any(token in url for token in ("watermark", "logo", "placeholder", "no-image", "noimage")):
        return 0.3
    return 1.0


def score_offer(offer: OfferReadModel, trust_score: float) -> Candidate:
    weights = settings.REPRESENTATIVE_WEIGHTS
    cleaned = strip_promotional(offer.title_normalized)
    components = {
        "completeness": completeness(offer),
        "seller_trust": max(0.0, min(1.0, trust_score)),
        "image": _image_score(offer),
        "title_band": 1.0 if is_sane_title(cleaned, settings.TITLE_LENGTH_BAND) else 0.0,
    }
    total = sum(weights[name] * value for name, value in components.items())
    return Candidate(offer.offer_uid, round(total, 6), components)


def elect(
    offers: Sequence[OfferReadModel],
    trust_by_seller: dict[str, float],
    *,
    forced_offer_uid: str | None = None,
) -> Election:
    """Pick the representative and derive the product's title and image.

    ``forced_offer_uid`` is a human's pick and wins outright while that offer
    is still a member — human decisions are sticky, and a reprocess must
    never quietly erase one.
    """
    dominant = dominant_authenticity(offers)
    if not offers:
        return Election(None, "", None, dominant, {"rule": "no_members"})

    by_uid = {o.offer_uid: o for o in offers}

    if forced_offer_uid and forced_offer_uid in by_uid:
        chosen = by_uid[forced_offer_uid]
        title, title_source = _title_for(chosen, offers)
        return Election(
            chosen.offer_uid,
            title,
            chosen.image_url,
            dominant,
            {"rule": "human_override", "title_source": title_source},
        )

    # The authenticity gate: prefer members that agree with the cluster's
    # dominant claim, and only fall back to the whole cluster when none do.
    matching = [o for o in offers if o.authenticity_claim == dominant]
    pool = matching or list(offers)
    gate = "authenticity_dominant" if matching else "no_matching_claim"

    scored = sorted(
        (score_offer(o, trust_by_seller.get(o.seller_key, 0.0)) for o in pool),
        # Highest score wins; offer_uid breaks ties so re-election is stable.
        key=lambda c: (-c.score, c.offer_uid),
    )
    winner = scored[0]
    chosen = by_uid[winner.offer_uid]
    title, title_source = _title_for(chosen, offers)

    return Election(
        chosen.offer_uid,
        title,
        chosen.image_url,
        dominant,
        {
            "rule": "scored",
            "gate": gate,
            "candidates_considered": len(pool),
            "winning_score": winner.score,
            "components": winner.components,
            "runner_up": scored[1].offer_uid if len(scored) > 1 else None,
            "title_source": title_source,
        },
    )


def _title_for(chosen: OfferReadModel, offers: Sequence[OfferReadModel]) -> tuple[str, str]:
    """Derive the product title, defensively.

    Always from ``title_normalized``; ``raw_title`` is only ever a last
    resort, and even then it goes through the junk stripper first. The result
    is guaranteed non-empty as long as the cluster has any member with any
    text at all, because a product with an empty title is a broken page.
    """
    candidate = strip_promotional(chosen.title_normalized)
    if candidate:
        return candidate, "representative.title_normalized"

    # The representative's title was entirely promotional. Fall back through
    # the other members before ever touching a raw title.
    for other in sorted(offers, key=lambda o: o.offer_uid):
        fallback = strip_promotional(other.title_normalized)
        if fallback:
            return fallback, f"member.title_normalized:{other.offer_uid}"

    for source in (chosen, *sorted(offers, key=lambda o: o.offer_uid)):
        raw = strip_promotional(source.raw_title)
        if raw:
            return raw, f"raw_title:{source.offer_uid}"

    return "", "none"
