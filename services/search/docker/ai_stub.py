from __future__ import annotations

import hashlib
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def embedding(text: str) -> list[float]:
    aliases = {
        "توپی چرخ": "بلبرینگ چرخ",
        "ذغال استارت": "زغال استارت",
        "لوازم جلوبندی": "قطعات سیستم تعلیق جلو",
    }
    semantic_text = text
    for colloquial, technical in aliases.items():
        semantic_text = semantic_text.replace(colloquial, technical)
    vector = [0.0] * 384
    for token in semantic_text.casefold().split():
        digest = hashlib.sha256(token.encode()).digest()
        for index, byte in enumerate(digest):
            vector[(index * 13 + byte) % 384] += 1.0 if byte % 2 else -1.0
    magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / magnitude for value in vector]


class Handler(BaseHTTPRequestHandler):
    def _write(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write(200, {"status": "ok", "dim": 384})
            return
        self._write(404, {"code": "not_found", "message": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/embed":
            self._write(404, {"code": "not_found", "message": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        texts = body.get("texts")
        if not isinstance(texts, list) or not 1 <= len(texts) <= 256:
            self._write(422, {"code": "invalid_request", "message": "texts is invalid"})
            return
        self._write(
            200,
            {
                "vectors": [embedding(str(text)) for text in texts],
                "dim": 384,
                "model": "deterministic-search-stub",
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        del format, args


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
