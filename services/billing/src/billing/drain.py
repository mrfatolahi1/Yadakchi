from __future__ import annotations

import logging

import redis

from billing.click_queue import (
    acknowledge_click,
    claim_click,
    parse_queued_click,
    recover_processing,
    requeue_click,
)
from billing.producer import Publisher, publish_pending
from billing.wallet import SellerUnavailableError, process_queued_click

logger = logging.getLogger(__name__)


def drain_clicks(
    *,
    limit: int = 100,
    redis_client: redis.Redis | None = None,
    publisher: Publisher | None = None,
    recover: bool = True,
) -> int:
    if recover:
        recover_processing(redis_client)
    processed = 0
    for _ in range(limit):
        raw = claim_click(redis_client)
        if raw is None:
            break
        try:
            queued = parse_queued_click(raw)
            process_queued_click(queued)
        except SellerUnavailableError:
            requeue_click(raw, redis_client)
            logger.warning(
                "seller unavailable while draining click",
                extra={"event": "click_drain_seller_unavailable"},
            )
            break
        except Exception:
            requeue_click(raw, redis_client)
            logger.exception("click drain failed", extra={"event": "click_drain_failed"})
            raise
        acknowledge_click(raw, redis_client)
        processed += 1
    publish_pending(publisher)
    return processed
