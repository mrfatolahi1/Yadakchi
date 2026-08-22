from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-search-only-secret")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [host for host in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",") if host]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "search.apps.SearchConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "search.urls"
WSGI_APPLICATION = "search.wsgi.application"
ASGI_APPLICATION = "search.asgi.application"


def database_config() -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        parsed = urlparse(database_url)
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": parsed.port or 5432,
            "CONN_MAX_AGE": 60,
        }
    if os.getenv("SEARCH_DB_HOST"):
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("SEARCH_DB_NAME", "yadakchi_search"),
            "USER": os.getenv("SEARCH_DB_USER", "yadakchi_search"),
            "PASSWORD": os.getenv("SEARCH_DB_PASSWORD", ""),
            "HOST": os.environ["SEARCH_DB_HOST"],
            "PORT": int(os.getenv("SEARCH_DB_PORT", "5432")),
            "CONN_MAX_AGE": 60,
        }
    return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}


DATABASES = {"default": database_config()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "fa-ir"

TYPESENSE_URL = os.getenv("TYPESENSE_URL", "http://localhost:8108").rstrip("/")
TYPESENSE_API_KEY = os.getenv("TYPESENSE_API_KEY", "search-local-key")
TYPESENSE_COLLECTION = os.getenv("TYPESENSE_COLLECTION", "products")
AI_BASE_URL = os.getenv("AI_BASE_URL", "http://localhost:8001").rstrip("/")
AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "10"))
SEARCH_REDIS_URL = os.getenv("SEARCH_REDIS_URL", "redis://localhost:6379/5")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
SEARCH_RESULT_FLOOR = int(os.getenv("SEARCH_RESULT_FLOOR", "5"))
SEARCH_PAGE_SIZE = int(os.getenv("SEARCH_PAGE_SIZE", "20"))
SEARCH_CANDIDATE_LIMIT = int(os.getenv("SEARCH_CANDIDATE_LIMIT", "250"))
SEARCH_REINDEX_RATE_PER_SECOND = float(os.getenv("SEARCH_REINDEX_RATE_PER_SECOND", "20"))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("trace_id", "event_type", "product_uid", "query_id"):
            value = getattr(record, key, None)
            if value is not None:
                body[key] = value
        if record.exc_info:
            body["exception"] = self.formatException(record.exc_info)
        return json.dumps(body, ensure_ascii=False)


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "search.settings.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
