from __future__ import annotations

from uuid import uuid4

import pytest

from search.models import QueryLog
from search.query_log import record_click


@pytest.mark.django_db
def test_result_click_records_the_reported_position() -> None:
    first = uuid4()
    second = uuid4()
    query = QueryLog.objects.create(
        normalized_text="لنت ترمز",
        filters={},
        result_count=2,
        result_product_uids=[str(first), str(second)],
    )

    assert record_click(query.query_id, second, 2) is True
    query.refresh_from_db()
    assert query.clicked_position == 2
    assert query.clicked_product_uid == second


@pytest.mark.django_db
def test_mismatched_result_click_is_ignored() -> None:
    product_uid = uuid4()
    query = QueryLog.objects.create(
        normalized_text="لنت ترمز",
        filters={},
        result_count=1,
        result_product_uids=[str(product_uid)],
    )

    assert record_click(query.query_id, uuid4(), 1) is False
    query.refresh_from_db()
    assert query.clicked_position is None
