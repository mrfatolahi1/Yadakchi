from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class TokenError(ValueError):
    pass


class ClickIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    product_uid: UUID
    offer_uid: str = Field(pattern=r"^[0-9a-f]{32}$")
    seller_key: str = Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    destination_url: str
    price_toman: int | None = Field(default=None, ge=0)
    is_panel_offer: bool
    issued_at: int
    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("destination_url")
    @classmethod
    def validate_destination(cls, value: str) -> str:
        if not value.isascii() or any(ord(character) < 32 for character in value):
            raise ValueError("destination_url must be an ASCII URL without control characters")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("destination_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("destination_url must not contain credentials")
        return value


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise TokenError("invalid token encoding") from exc


def _sign(encoded_payload: str, key: str) -> str:
    if not key:
        raise RuntimeError("CLICK_SIGNING_KEY is required")
    digest = hmac.new(key.encode(), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def sign_click_intent(intent: ClickIntent, *, key: str | None = None) -> str:
    payload = intent.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    encoded_payload = _b64url_encode(raw)
    return f"{encoded_payload}.{_sign(encoded_payload, key or settings.CLICK_SIGNING_KEY)}"


def verify_click_token(
    token: str,
    *,
    key: str | None = None,
    now: int | None = None,
    ttl_seconds: int | None = None,
) -> ClickIntent:
    try:
        encoded_payload, supplied_signature = token.split(".", maxsplit=1)
    except ValueError as exc:
        raise TokenError("invalid token") from exc

    expected_signature = _sign(encoded_payload, key or settings.CLICK_SIGNING_KEY)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise TokenError("invalid token")

    try:
        intent = ClickIntent.model_validate_json(_b64url_decode(encoded_payload))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise TokenError("invalid token payload") from exc

    current_time = int(time.time()) if now is None else now
    token_ttl = settings.CLICK_TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    if intent.issued_at > current_time + settings.CLICK_TOKEN_FUTURE_SKEW_SECONDS:
        raise TokenError("token issued in the future")
    if current_time - intent.issued_at > token_ttl:
        raise TokenError("token expired")
    return intent
