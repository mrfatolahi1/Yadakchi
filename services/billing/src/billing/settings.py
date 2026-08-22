from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [item for item in os.getenv("ALLOWED_HOSTS", "*").split(",") if item]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "billing.apps.BillingConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "billing.urls"
ASGI_APPLICATION = "billing.asgi.application"
WSGI_APPLICATION = "billing.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]


def database_config(database_url: str) -> dict[str, object]:
    parsed = urlparse(database_url)
    if parsed.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": parsed.port or 5432,
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"connect_timeout": 2},
        }
    if parsed.scheme == "sqlite":
        path = parsed.path or str(BASE_DIR / "db.sqlite3")
        if path == "/:memory:":
            path = ":memory:"
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": path}
    raise ValueError("DATABASE_URL must use postgresql:// or sqlite://")


DATABASES = {
    "default": database_config(os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"))
}

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

BILLING_REDIS_URL = os.getenv("BILLING_REDIS_URL", "")
if not BILLING_REDIS_URL:
    raise ImproperlyConfigured("BILLING_REDIS_URL is required")
REDIS_HOT_PATH_TIMEOUT_SECONDS = float(os.getenv("REDIS_HOT_PATH_TIMEOUT_SECONDS", "0.04"))
CLICK_SIGNING_KEY = os.getenv("CLICK_SIGNING_KEY", "")
PRIVACY_HASH_KEY = os.getenv("PRIVACY_HASH_KEY", "")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")
KAFKA_CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "billing-sellers-v1")

CLICK_TOKEN_TTL_SECONDS = int(os.getenv("CLICK_TOKEN_TTL_SECONDS", "1800"))
CLICK_TOKEN_FUTURE_SKEW_SECONDS = int(os.getenv("CLICK_TOKEN_FUTURE_SKEW_SECONDS", "60"))
IP_RATE_LIMIT = int(os.getenv("IP_RATE_LIMIT", "10"))
IP_RATE_WINDOW_SECONDS = int(os.getenv("IP_RATE_WINDOW_SECONDS", "300"))
FINGERPRINT_REPEAT_SECONDS = int(os.getenv("FINGERPRINT_REPEAT_SECONDS", "1800"))
SELLER_VELOCITY_LIMIT = int(os.getenv("SELLER_VELOCITY_LIMIT", "200"))
SELLER_VELOCITY_WINDOW_SECONDS = int(os.getenv("SELLER_VELOCITY_WINDOW_SECONDS", "300"))
PUBLIC_REFERER_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv("PUBLIC_REFERER_HOSTS", "yadakchi.ir,www.yadakchi.ir").split(",")
    if host.strip()
)
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
NON_PANEL_TRUST_CAP = float(os.getenv("NON_PANEL_TRUST_CAP", "0.65"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "billing.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
