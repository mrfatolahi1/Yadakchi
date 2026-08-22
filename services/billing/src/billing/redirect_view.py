from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import redis
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.views.decorators.http import require_GET

from billing.click_queue import QueuedClick, enqueue_click
from billing.fraud import client_ip, request_hashes, static_fraud_reasons
from billing.tokens import TokenError, verify_click_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RedirectResult:
    status_code: int
    destination_url: str | None = None


def _utc_string(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def process_redirect(
    token: str,
    *,
    headers: dict[str, str],
    remote_addr: str,
    now: int | None = None,
) -> RedirectResult:
    timestamp = int(time.time()) if now is None else now
    try:
        intent = verify_click_token(token, now=timestamp)
    except TokenError:
        return RedirectResult(status_code=400)

    ip_address = client_ip(headers, remote_addr)
    user_agent = headers.get("user-agent", "")
    ip_hash, user_agent_hash, fingerprint_hash = request_hashes(ip_address, user_agent)
    click_id = uuid.uuid4()
    queued = QueuedClick(
        click_id=str(click_id),
        product_uid=str(intent.product_uid),
        offer_uid=intent.offer_uid,
        seller_key=intent.seller_key,
        price_toman=intent.price_toman,
        is_panel_offer=intent.is_panel_offer,
        occurred_at=_utc_string(timestamp),
        trace_id=str(click_id),
        ip_hash=ip_hash,
        user_agent_hash=user_agent_hash,
        fingerprint_hash=fingerprint_hash,
    )
    try:
        accepted, _ = enqueue_click(
            queued,
            nonce=intent.nonce,
            base_reasons=static_fraud_reasons(user_agent, headers.get("referer")),
            now=timestamp,
        )
    except redis.RedisError:
        logger.exception("redirect queue unavailable", extra={"event": "redirect_queue_error"})
        return RedirectResult(status_code=503)
    if not accepted:
        return RedirectResult(status_code=400)
    return RedirectResult(status_code=302, destination_url=intent.destination_url)


def _response_for(result: RedirectResult) -> HttpResponse:
    if result.status_code == 302 and result.destination_url:
        response: HttpResponse = HttpResponseRedirect(result.destination_url)
    else:
        response = HttpResponse(status=result.status_code)
    response["Cache-Control"] = "no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_GET
def redirect_view(request: HttpRequest, token: str) -> HttpResponse:
    headers = {key.lower(): value for key, value in request.headers.items()}
    remote_addr = str(request.META.get("REMOTE_ADDR", ""))
    return _response_for(process_redirect(token, headers=headers, remote_addr=remote_addr))


@require_GET
def health_view(_request: HttpRequest) -> HttpResponse:
    return HttpResponse('{"status":"ok"}', content_type="application/json")
