"""yadakchi `ai` — the only service in the system that talks to a model."""

from __future__ import annotations

#: Version of the HTTP contract published to contracts/published/openapi.json.
#: Bump deliberately: `enricher`, `matcher` and `search` generate clients from
#: that document.
API_VERSION = "1.0.0"

__all__ = ["API_VERSION"]
