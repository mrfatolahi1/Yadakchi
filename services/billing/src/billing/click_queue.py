from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any, cast

import redis
from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field

PENDING_QUEUE = "billing:clicks:pending"
PROCESSING_QUEUE = "billing:clicks:processing"

ENQUEUE_SCRIPT = r"""
if not redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1]) then
    return {0, 0, 0}
end

local reasons = cjson.decode(ARGV[7])

local ip_count = redis.call('INCR', KEYS[2])
if ip_count == 1 then redis.call('EXPIRE', KEYS[2], ARGV[2]) end
if ip_count > tonumber(ARGV[3]) then table.insert(reasons, 'ip_rate_limit') end

if not redis.call('SET', KEYS[3], '1', 'NX', 'EX', ARGV[4]) then
    table.insert(reasons, 'repeat_fingerprint')
end

local velocity_count = redis.call('INCR', KEYS[4])
if velocity_count == 1 then redis.call('EXPIRE', KEYS[4], ARGV[5]) end
local velocity_exceeded = velocity_count > tonumber(ARGV[6])
local review_requested = false
if velocity_exceeded then
    table.insert(reasons, 'seller_velocity')
    review_requested = redis.call('SET', KEYS[5], '1', 'NX', 'EX', ARGV[5]) ~= false
end

local click = cjson.decode(ARGV[8])
click['fraud_reasons'] = reasons
click['is_suspicious'] = #reasons > 0
click['velocity_anomaly'] = review_requested
click['velocity_count'] = velocity_count
click['velocity_bucket'] = ARGV[9]
redis.call('LPUSH', KEYS[6], cjson.encode(click))

return {1, click['is_suspicious'] and 1 or 0, review_requested and 1 or 0}
"""


class QueuedClick(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    click_id: str
    product_uid: str
    offer_uid: str = Field(pattern=r"^[0-9a-f]{32}$")
    seller_key: str
    price_toman: int | None = Field(default=None, ge=0)
    is_panel_offer: bool
    occurred_at: str
    trace_id: str
    ip_hash: str = Field(min_length=64, max_length=64)
    user_agent_hash: str = Field(min_length=64, max_length=64)
    fingerprint_hash: str = Field(min_length=64, max_length=64)
    fraud_reasons: list[str] = Field(default_factory=list)
    is_suspicious: bool = False
    velocity_anomaly: bool = False
    velocity_count: int = 0
    velocity_bucket: str = ""


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(
        settings.BILLING_REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=settings.REDIS_HOT_PATH_TIMEOUT_SECONDS,
        socket_timeout=settings.REDIS_HOT_PATH_TIMEOUT_SECONDS,
        retry_on_timeout=False,
    )


def enqueue_click(
    click: QueuedClick,
    *,
    nonce: str,
    base_reasons: list[str],
    redis_client: redis.Redis | None = None,
    now: int | None = None,
) -> tuple[bool, bool]:
    client = redis_client or get_redis()
    timestamp = int(time.time()) if now is None else now
    velocity_bucket = str(timestamp // settings.SELLER_VELOCITY_WINDOW_SECONDS)
    keys = [
        f"billing:nonce:{nonce}",
        f"billing:fraud:ip:{click.seller_key}:{click.ip_hash}",
        f"billing:fraud:repeat:{click.offer_uid}:{click.fingerprint_hash}",
        f"billing:fraud:velocity:{click.seller_key}",
        f"billing:fraud:velocity-review:{click.seller_key}:{velocity_bucket}",
        PENDING_QUEUE,
    ]
    args = [
        str(settings.CLICK_TOKEN_TTL_SECONDS),
        str(settings.IP_RATE_WINDOW_SECONDS),
        str(settings.IP_RATE_LIMIT),
        str(settings.FINGERPRINT_REPEAT_SECONDS),
        str(settings.SELLER_VELOCITY_WINDOW_SECONDS),
        str(settings.SELLER_VELOCITY_LIMIT),
        json.dumps(base_reasons, separators=(",", ":")),
        click.model_dump_json(),
        velocity_bucket,
    ]
    result = cast(list[int], client.eval(ENQUEUE_SCRIPT, len(keys), *keys, *args))
    if not bool(result[0]):
        return False, False
    return True, bool(result[1])


def recover_processing(redis_client: redis.Redis | None = None) -> int:
    client = redis_client or get_redis()
    recovered = 0
    while client.rpoplpush(PROCESSING_QUEUE, PENDING_QUEUE):
        recovered += 1
    return recovered


def claim_click(redis_client: redis.Redis | None = None) -> str | None:
    client = redis_client or get_redis()
    return cast(str | None, client.rpoplpush(PENDING_QUEUE, PROCESSING_QUEUE))


def acknowledge_click(raw: str, redis_client: redis.Redis | None = None) -> None:
    client = redis_client or get_redis()
    client.lrem(PROCESSING_QUEUE, 1, raw)


def requeue_click(raw: str, redis_client: redis.Redis | None = None) -> None:
    client = redis_client or get_redis()
    pipeline = client.pipeline(transaction=True)
    pipeline.lrem(PROCESSING_QUEUE, 1, raw)
    pipeline.lpush(PENDING_QUEUE, raw)
    pipeline.execute()


def parse_queued_click(raw: bytes | str) -> QueuedClick:
    value: Any = json.loads(raw)
    return QueuedClick.model_validate(value)
