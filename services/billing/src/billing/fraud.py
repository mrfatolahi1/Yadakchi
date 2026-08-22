from __future__ import annotations

import hashlib
import hmac
import re
from urllib.parse import urlsplit

from django.conf import settings

BOT_PATTERN = re.compile(
    r"bot|crawler|spider|slurp|headless|facebookexternalhit|bingpreview|curl|wget",
    re.IGNORECASE,
)


def privacy_hash(value: str) -> str:
    if not settings.PRIVACY_HASH_KEY:
        raise RuntimeError("PRIVACY_HASH_KEY is required")
    return hmac.new(settings.PRIVACY_HASH_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def request_hashes(ip_address: str, user_agent: str) -> tuple[str, str, str]:
    ip_hash = privacy_hash(ip_address)
    user_agent_hash = privacy_hash(user_agent)
    fingerprint_hash = privacy_hash(f"{ip_hash}:{user_agent_hash}")
    return ip_hash, user_agent_hash, fingerprint_hash


def static_fraud_reasons(user_agent: str, referer: str | None) -> list[str]:
    reasons: list[str] = []
    if BOT_PATTERN.search(user_agent):
        reasons.append("known_bot")

    if not referer:
        reasons.append("missing_referer")
        return reasons

    hostname = (urlsplit(referer).hostname or "").lower()
    allowed = any(
        hostname == host or hostname.endswith(f".{host}") for host in settings.PUBLIC_REFERER_HOSTS
    )
    if not allowed:
        reasons.append("foreign_referer")
    return reasons


def client_ip(headers: dict[str, str], remote_addr: str) -> str:
    if settings.TRUST_PROXY_HEADERS:
        forwarded = headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", maxsplit=1)[0].strip()
    return remote_addr
