from __future__ import annotations

from django.apps import AppConfig


class FitmentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fitment"

    def ready(self) -> None:
        from fitment import signals  # noqa: F401
