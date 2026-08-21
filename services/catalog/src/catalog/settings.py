"""Django settings for the catalog service.

No secrets in code: every credential arrives through the environment, exactly
as platform/.env hands it to the container.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None else value.strip()


def _env_word(name: str, default: str = "") -> str:
    """A single-token setting, tolerant of how the value reached us.

    platform/.env writes trailing comments on the same line as a value
    (`LOG_FORMAT=json  # structured JSON to stdout`), and Docker Compose
    hands some of them to the container intact. A setting that must be one
    word takes the first word and ignores the prose.
    """
    raw = _env(name, default).split("#", 1)[0].strip()
    return raw.split()[0] if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_word(name)
    return raw.lower() in {"1", "true", "yes", "on"} if raw else default


def _env_int(name: str, default: int) -> int:
    raw = _env_word(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env_word(name)
    return float(raw) if raw else default


# --------------------------------------------------------------------- core
SECRET_KEY = _env("DJANGO_SECRET_KEY", "dev-only-not-a-secret-catalog")
DEBUG = _env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [h for h in _env("DJANGO_ALLOWED_HOSTS", "*").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "catalog.urls"
WSGI_APPLICATION = "catalog.wsgi.application"

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


# ----------------------------------------------------------------- database
def _database_from_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/") or "yadakchi_catalog",
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "postgres",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": _env_int("DB_CONN_MAX_AGE", 60),
        "OPTIONS": {"application_name": "catalog"},
        "TEST": {"NAME": _env("DJANGO_TEST_DB_NAME", "test_yadakchi_catalog")},
    }


DATABASES = {
    "default": _database_from_url(
        _env("DATABASE_URL", "postgres://catalog:catalog@postgres:5432/yadakchi_catalog")
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -------------------------------------------------------------------- redis
# The URL is taken from the environment and never hardcoded. platform/.env
# assigns catalog Redis db 4 (SPEC.md's "db 5" is stale — db 5 belongs to
# search); reading REDIS_URL keeps this service correct either way.
REDIS_URL = _env("REDIS_URL", "redis://redis:6379/4")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "catalog",
    }
}

# -------------------------------------------------------------------- kafka
KAFKA_BOOTSTRAP_SERVERS = _env_word("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_SECURITY_PROTOCOL = _env_word("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
KAFKA_CONSUMER_GROUP_PREFIX = _env("KAFKA_CONSUMER_GROUP_PREFIX", "catalog")

TOPIC_CLUSTERS_CHANGED = "yadakchi.clusters.changed.v1"
TOPIC_OFFERS_ENRICHED = "yadakchi.offers.enriched.v1"
TOPIC_OFFERS_FITTED = "yadakchi.offers.fitted.v1"
TOPIC_VEHICLES_CHANGED = "yadakchi.vehicles.changed.v1"
TOPIC_CROSSREFS_CHANGED = "yadakchi.crossrefs.changed.v1"
TOPIC_CLICKS_RECORDED = "yadakchi.clicks.recorded.v1"
TOPIC_PRODUCTS_CHANGED = "yadakchi.products.changed.v1"
TOPIC_SELLERS_CHANGED = "yadakchi.sellers.changed.v1"

# "kafka" produces for real; "memory" collects in-process (tests, dry runs).
EVENT_TRANSPORT = _env_word("CATALOG_EVENT_TRANSPORT", "kafka")

# ------------------------------------------------------------------- celery
CELERY_BROKER_URL = _env("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = _env("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = _env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TIMEZONE = "UTC"

# ---------------------------------------------------------------- behaviour
# Every weight and threshold below is configuration, per SPEC.md part three.

#: A products.changed event is emitted at most once per this window, however
#: many material changes land inside it (acceptance criterion 9).
PRODUCT_EMIT_DEBOUNCE_SECONDS = _env_int("CATALOG_EMIT_DEBOUNCE_SECONDS", 60)

#: Representative selection weights. Completeness dominates; a sane title
#: length only breaks ties.
REPRESENTATIVE_WEIGHTS = {
    "completeness": _env_float("CATALOG_REP_W_COMPLETENESS", 0.50),
    "seller_trust": _env_float("CATALOG_REP_W_TRUST", 0.25),
    "image": _env_float("CATALOG_REP_W_IMAGE", 0.20),
    "title_band": _env_float("CATALOG_REP_W_TITLE_BAND", 0.05),
}
#: Titles shorter or longer than this band score 0 on the title-band signal.
TITLE_LENGTH_BAND = (
    _env_int("CATALOG_TITLE_MIN", 12),
    _env_int("CATALOG_TITLE_MAX", 90),
)

#: Offer ranking weights. Trust first, then price, then stock, then freshness
#: — SPEC.md part three. CPC is deliberately absent and must stay absent.
#: The `trust` weight is reported in the score breakdown but does not compete
#: with the others: trust is the primary sort key, not a summand. See
#: catalog.ranking for why a flat weighted sum cannot express "trust first".
RANKING_WEIGHTS = {
    "trust": _env_float("CATALOG_RANK_W_TRUST", 0.60),
    "price": _env_float("CATALOG_RANK_W_PRICE", 0.20),
    "stock": _env_float("CATALOG_RANK_W_STOCK", 0.15),
    "freshness": _env_float("CATALOG_RANK_W_FRESHNESS", 0.05),
}
#: Trust scores closer together than this are treated as equal, and price
#: decides between them. Widening it lets price matter more; narrowing it
#: makes the ordering more strictly trust-driven.
RANKING_TRUST_BAND = _env_float("CATALOG_RANK_TRUST_BAND", 0.05)
#: Price freshness decays to half after this many days.
PRICE_FRESHNESS_HALFLIFE_DAYS = _env_float("CATALOG_FRESHNESS_HALFLIFE_DAYS", 7.0)

#: Trust weights. Price and stock accuracy are 70% of the score between them
#: because they are the only signals we observe ourselves and a seller cannot
#: fake them (SPEC.md part four).
TRUST_WEIGHTS = {
    "price_accuracy": _env_float("CATALOG_TRUST_W_PRICE", 0.40),
    "stock_accuracy": _env_float("CATALOG_TRUST_W_STOCK", 0.30),
    "panel": _env_float("CATALOG_TRUST_W_PANEL", 0.10),
    "domain_age": _env_float("CATALOG_TRUST_W_DOMAIN_AGE", 0.08),
    "contact": _env_float("CATALOG_TRUST_W_CONTACT", 0.07),
    "badge": _env_float("CATALOG_TRUST_W_BADGE", 0.05),
}
#: Value an unobserved signal contributes. Never 1.0: an unknown signal must
#: not look like a perfect one.
TRUST_NEUTRAL_PRIOR = _env_float("CATALOG_TRUST_NEUTRAL_PRIOR", 0.5)
#: Beta-smoothing strength for accuracy ratios. One lucky observation must not
#: buy a seller a perfect score.
TRUST_SMOOTHING_STRENGTH = _env_float("CATALOG_TRUST_SMOOTHING", 5.0)
#: Below this many observations we publish accuracy as null and keep the
#: seller in the "new" tier: visibility is earned.
TRUST_MIN_OBSERVATIONS = _env_int("CATALOG_TRUST_MIN_OBSERVATIONS", 10)
#: The cold-start ceiling per tier. "new" is capped on purpose.
TRUST_TIER_CEILING = {
    "new": _env_float("CATALOG_TRUST_CEILING_NEW", 0.45),
    "standard": _env_float("CATALOG_TRUST_CEILING_STANDARD", 0.80),
    "trusted": _env_float("CATALOG_TRUST_CEILING_TRUSTED", 1.00),
    "suspended": _env_float("CATALOG_TRUST_CEILING_SUSPENDED", 0.15),
}
#: Promotion to "trusted" needs both accuracies at or above this.
TRUST_TRUSTED_THRESHOLD = _env_float("CATALOG_TRUST_TRUSTED_THRESHOLD", 0.90)
#: ...and this many observations.
TRUST_TRUSTED_MIN_OBSERVATIONS = _env_int("CATALOG_TRUST_TRUSTED_MIN_OBSERVATIONS", 50)

#: Days of price history carried in the payload so web can draw the chart.
PRICE_SERIES_WINDOW_DAYS = _env_int("CATALOG_PRICE_SERIES_DAYS", 90)

#: How many related products ride along on the read API.
RELATED_PRODUCTS_LIMIT = _env_int("CATALOG_RELATED_LIMIT", 8)

#: Publication gate (SPEC.md part six). A thin page is a domain-wide SEO
#: liability, so a product needs real substance, not just a row.
PUBLICATION_MIN_SUBSTANCE_FACTS = _env_int("CATALOG_MIN_SUBSTANCE_FACTS", 1)
PUBLICATION_REQUIRE_PRICE = _env_bool("CATALOG_REQUIRE_PRICE", True)

#: ProcessedEvent rows older than this are pruned. The read models' own
#: occurred_at guard keeps replay-after-pruning correct.
PROCESSED_EVENT_RETENTION_DAYS = _env_int("CATALOG_PROCESSED_EVENT_RETENTION_DAYS", 30)

#: PriceHistory partitions created ahead of time by `make_partitions`.
PARTITION_MONTHS_AHEAD = _env_int("CATALOG_PARTITION_MONTHS_AHEAD", 3)

SERVICE_NAME = "catalog"

# --------------------------------------------------------------------- i18n
LANGUAGE_CODE = "fa"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR.parent / "staticfiles"

# ------------------------------------------------------------------ logging
LOG_LEVEL = _env_word("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = _env_word("LOG_FORMAT", "json").lower()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "catalog.logging_config.JsonFormatter"},
        "plain": {"format": "%(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json" if LOG_FORMAT == "json" else "plain",
        }
    },
    "root": {"handlers": ["stdout"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["stdout"], "propagate": False},
        "catalog": {"level": LOG_LEVEL, "handlers": ["stdout"], "propagate": False},
    },
}

# ------------------------------------------------------------------- sentry
# An empty DSN disables Sentry. The scheme check is not paranoia: the value
# arrives from a shared env file that carries trailing comments, and a
# malformed DSN must never take the service down at import time.
SENTRY_DSN = _env_word("SENTRY_DSN")
if "://" in SENTRY_DSN:  # pragma: no cover - exercised only with a real DSN
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=_env_word("SENTRY_ENVIRONMENT", "local"),
        traces_sample_rate=_env_float("SENTRY_TRACES_SAMPLE_RATE", 0.05),
    )
