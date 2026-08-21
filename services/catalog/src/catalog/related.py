"""Related parts.

SPEC.md part six is blunt about why this exists: the indexing policy is
"index everything", so a thin page is not merely a weak page, it is a
domain-wide SEO liability. Even a single-offer product has to carry fitment,
cross-reference equivalents, price history *and* related parts.

Related parts ride on the **read API only**. ``products.changed.v1`` has no
field for them, and a published payload shape is not changed without a
version bump — so they are computed here, stored on the product row, and
served by ``GET /v1/products/{slug}``, which is what `web` renders from.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db.models import Q

from catalog.models import Product


def _card(product: Product) -> dict[str, Any]:
    return {
        "product_uid": str(product.product_uid),
        "slug": product.slug,
        "title": product.title,
        "image_url": product.image_url,
        "min_price_toman": product.min_price_toman,
        "offer_count": product.offer_count,
    }


def find_related(
    product_uid: uuid.UUID,
    *,
    part_type: str | None,
    vehicles_compatible: list[str],
    crossref_codes: list[str],
    part_numbers: list[str],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Products a visitor to this page plausibly wants next.

    Relatedness is scored in three descending tiers, and the query is a
    single bounded fetch rather than one per tier:

    1. same part type **and** an overlapping vehicle — the strongest signal,
       because it is the same job on the same car;
    2. a shared part number or cross-reference code — an equivalent part
       from another brand;
    3. same part type — the weak fallback that keeps a lonely page from
       being thin.

    Ties break on traffic, then offer count, then uid, so the list is stable
    between rebuilds of unchanged data.
    """
    limit = limit or settings.RELATED_PRODUCTS_LIMIT
    codes = [c for c in {*crossref_codes, *part_numbers} if c]

    criteria = Q()
    if part_type:
        criteria |= Q(part_type=part_type)
    if codes:
        criteria |= Q(crossref_codes__overlap=codes) | Q(part_numbers__overlap=codes)
    if not criteria:
        return []

    candidates = (
        Product.objects.filter(criteria)
        .filter(is_published=True, successor_product_uid__isnull=True)
        .exclude(product_uid=product_uid)
        .only(
            "product_uid",
            "slug",
            "title",
            "image_url",
            "min_price_toman",
            "offer_count",
            "part_type",
            "vehicles_compatible",
            "crossref_codes",
            "part_numbers",
            "click_count",
        )[: limit * 4]
    )

    vehicles = set(vehicles_compatible)
    code_set = set(codes)

    def tier(candidate: Product) -> int:
        shares_vehicle = bool(vehicles & set(candidate.vehicles_compatible or []))
        same_type = bool(part_type) and candidate.part_type == part_type
        if same_type and shares_vehicle:
            return 0
        if code_set & {*(candidate.crossref_codes or []), *(candidate.part_numbers or [])}:
            return 1
        return 2

    ordered = sorted(
        candidates,
        key=lambda c: (tier(c), -c.click_count, -c.offer_count, str(c.product_uid)),
    )
    return [_card(c) for c in ordered[:limit]]
