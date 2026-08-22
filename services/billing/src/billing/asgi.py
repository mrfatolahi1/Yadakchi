from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import unquote

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing.settings")

from django.core.asgi import get_asgi_application  # noqa: E402

from billing.redirect_view import process_redirect  # noqa: E402

django_application = get_asgi_application()

AsgiReceive = Callable[[], Awaitable[dict[str, Any]]]
AsgiSend = Callable[[Mapping[str, Any]], Awaitable[None]]


async def _send_response(
    send: AsgiSend, status: int, body: bytes = b"", headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers or [],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def application(scope: dict[str, Any], receive: AsgiReceive, send: AsgiSend) -> None:
    if scope["type"] != "http":
        await django_application(scope, receive, send)
        return

    path = str(scope.get("path", ""))
    if path == "/healthz":
        await _send_response(
            send,
            200,
            b'{"status":"ok"}',
            [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        )
        return
    if not path.startswith("/go/"):
        await django_application(scope, receive, send)
        return
    if scope.get("method") != "GET":
        await _send_response(send, 405, headers=[(b"allow", b"GET")])
        return

    token = unquote(path.removeprefix("/go/"))
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    client = scope.get("client") or ("", 0)
    result = await asyncio.to_thread(
        process_redirect, token, headers=headers, remote_addr=str(client[0])
    )
    response_headers = [
        (b"cache-control", b"no-store, max-age=0"),
        (b"pragma", b"no-cache"),
        (b"x-robots-tag", b"noindex, nofollow"),
    ]
    if result.status_code == 302 and result.destination_url:
        response_headers.append((b"location", result.destination_url.encode("latin-1")))
    await _send_response(send, result.status_code, headers=response_headers)
