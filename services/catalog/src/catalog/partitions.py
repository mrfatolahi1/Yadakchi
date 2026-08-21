"""Monthly partitions for ``PriceHistory``.

Price history is the one table that grows without bound — every price move of
every offer, for ever — so it is range-partitioned by month. The parent table
is created by a migration; this module keeps months ahead of the clock and is
safe to run as often as you like.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import DatabaseError, connection, transaction

logger = logging.getLogger("catalog.partitions")

PARENT_TABLE = "catalog_pricehistory"
DEFAULT_PARTITION = f"{PARENT_TABLE}_default"


def _month_start(moment: dt.date) -> dt.date:
    return moment.replace(day=1)


def _next_month(moment: dt.date) -> dt.date:
    return (moment.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def partition_name(month: dt.date) -> str:
    return f"{PARENT_TABLE}_{month:%Y_%m}"


def ensure_partitions(months_ahead: int = 3, today: dt.date | None = None) -> list[str]:
    """Create this month's partition and the next ``months_ahead``.

    Idempotent: ``CREATE TABLE IF NOT EXISTS`` means running it twice is a
    no-op, which is what lets it sit on a schedule and also run at boot.
    """
    start = _month_start(today or dt.date.today())
    created: list[str] = []

    month = start
    for _ in range(months_ahead + 1):
        upper = _next_month(month)
        name = partition_name(month)
        # Each statement gets its own savepoint. Creating a partition fails
        # if rows for that month already sit in the default partition, and
        # that must not abort the whole run: the remaining months are still
        # worth provisioning, and the stranded rows are still queryable
        # through the parent.
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {PARENT_TABLE} "
                    "FOR VALUES FROM (%s) TO (%s)",
                    [month.isoformat(), upper.isoformat()],
                )
            created.append(name)
        except DatabaseError as exc:
            logger.warning(
                "partition not created; rows for this month are in the default partition",
                extra={"partition": name, "error": str(exc)},
            )
        month = upper

    logger.info("price history partitions ensured", extra={"partitions": created})
    return created
