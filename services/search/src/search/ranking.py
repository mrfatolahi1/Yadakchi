from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RankedDocument:
    document: dict[str, Any]
    score: float
    exact_part_number_match: bool


def _uid(document: dict[str, Any]) -> str:
    return str(document["product_uid"])


def _business_score(document: dict[str, Any], vehicle_slug: str | None) -> float:
    offer_count = max(0, int(document.get("offer_count", 0)))
    trust = max(0.0, min(1.0, float(document.get("best_seller_trust", 0.0))))
    image = 1.0 if document.get("has_image") else 0.0
    freshness_epoch = int(document.get("price_freshness", 0))
    freshness = 0.0
    if freshness_epoch > 0:
        age_days = max(0.0, (time.time() - freshness_epoch) / 86400)
        freshness = math.exp(-age_days / 30)
    fitment = 0.0
    compatible = document.get("vehicle_compatible", [])
    if vehicle_slug and isinstance(compatible, list) and vehicle_slug in compatible:
        fitment = 0.45
    return (
        min(math.log1p(offer_count), 4.0) * 0.025
        + image * 0.03
        + freshness * 0.04
        + trust * 0.06
        + fitment
    )


def _dedupe(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for document in documents:
        product_uid = _uid(document)
        if product_uid in seen:
            continue
        seen.add(product_uid)
        result.append(document)
    return result


def rank_documents(
    exact: list[dict[str, Any]],
    lexical: list[dict[str, Any]],
    vector: list[dict[str, Any]],
    vehicle_slug: str | None,
    rrf_k: int = 60,
) -> list[RankedDocument]:
    exact_documents = _dedupe(exact)
    exact_uids = {_uid(document) for document in exact_documents}
    exact_ranked = sorted(
        exact_documents,
        key=lambda document: _business_score(document, vehicle_slug),
        reverse=True,
    )

    scores: dict[str, float] = {}
    documents_by_uid: dict[str, dict[str, Any]] = {}
    for channel in (lexical, vector):
        for rank, document in enumerate(_dedupe(channel), start=1):
            product_uid = _uid(document)
            if product_uid in exact_uids:
                continue
            documents_by_uid[product_uid] = document
            scores[product_uid] = scores.get(product_uid, 0.0) + 1.0 / (rrf_k + rank)

    hybrid = [
        RankedDocument(
            document=document,
            score=scores[product_uid] + _business_score(document, vehicle_slug),
            exact_part_number_match=False,
        )
        for product_uid, document in documents_by_uid.items()
    ]
    hybrid.sort(key=lambda item: (-item.score, _uid(item.document)))
    return [
        RankedDocument(
            document=document,
            score=float("inf"),
            exact_part_number_match=True,
        )
        for document in exact_ranked
    ] + hybrid
