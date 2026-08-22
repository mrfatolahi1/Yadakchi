from __future__ import annotations

import logging
from uuid import UUID

from django.utils import timezone

from search.models import QueryLog
from search.query_builder import SearchFilters

logger = logging.getLogger(__name__)


def record_query(
    normalized_text: str,
    vehicle_slug: str | None,
    filters: SearchFilters,
    result_count: int,
    result_product_uids: list[str],
) -> QueryLog:
    query = QueryLog.objects.create(
        normalized_text=normalized_text,
        vehicle_slug=vehicle_slug,
        filters=filters.as_log_dict(),
        result_count=result_count,
        result_product_uids=result_product_uids,
    )
    logger.info("query logged", extra={"query_id": str(query.query_id)})
    return query


def record_click(query_id: UUID, product_uid: UUID, position: int) -> bool:
    query = QueryLog.objects.filter(query_id=query_id).first()
    if query is None or query.clicked_position is not None:
        return False
    expected_index = position - 1
    if expected_index >= len(query.result_product_uids):
        return False
    if str(query.result_product_uids[expected_index]) != str(product_uid):
        return False
    query.clicked_position = position
    query.clicked_product_uid = product_uid
    query.clicked_at = timezone.now()
    query.save(update_fields=("clicked_position", "clicked_product_uid", "clicked_at"))
    return True
