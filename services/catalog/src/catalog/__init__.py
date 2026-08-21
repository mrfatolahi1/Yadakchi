"""catalog — canonical products, sellers, trust and price history."""

from __future__ import annotations

from catalog.celery import app as celery_app

__all__ = ["celery_app"]
