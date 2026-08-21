import os
from pathlib import Path
from urllib.parse import quote

import dj_database_url

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "unsafe-local-crawler-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [host for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if host]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "crawler.apps.CrawlerConfig",
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

ROOT_URLCONF = "crawler.urls"
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
WSGI_APPLICATION = "crawler.wsgi.application"
ASGI_APPLICATION = "crawler.asgi.application"

database_password = os.environ.get("CRAWLER_DB_PASSWORD")
database_default = (
    f"postgresql://crawler:{quote(database_password, safe='')}@postgres:5432/yadakchi_crawler"
    if database_password
    else f"sqlite:///{BASE_DIR / 'crawler.sqlite3'}"
)
database_url = os.environ.get("CRAWLER_DATABASE_URL") or database_default
DATABASES = {
    "default": dj_database_url.parse(
        database_url,
        conn_max_age=60,
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

redis_password = os.environ.get("REDIS_PASSWORD")
redis_default = (
    f"redis://:{quote(redis_password, safe='')}@redis:6379/4"
    if redis_password
    else "redis://localhost:6379/4"
)
REDIS_URL = os.environ.get("CRAWLER_REDIS_URL", redis_default)
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_CLIENT_ID = os.environ.get("KAFKA_CLIENT_ID", "yadakchi-crawler")
KAFKA_CLICK_GROUP_ID = os.environ.get("KAFKA_CLICK_GROUP_ID", "crawler-click-tiering-v1")

MINIO_ENDPOINT_URL = os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000")
MINIO_ACCESS_KEY = (
    os.environ.get("MINIO_ACCESS_KEY") or os.environ.get("MINIO_ROOT_USER") or "minioadmin"
)
MINIO_SECRET_KEY = (
    os.environ.get("MINIO_SECRET_KEY") or os.environ.get("MINIO_ROOT_PASSWORD") or "minioadmin"
)
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "raw-archive")
MINIO_REGION = os.environ.get("MINIO_REGION", "us-east-1")

CRAWLER_USER_AGENT = os.environ.get(
    "CRAWLER_USER_AGENT", "YadakchiCrawler/1.0 (+https://yadakchi.ir/crawler)"
)
CRAWLER_PROXY_URL = os.environ.get("CRAWLER_PROXY_URL") or None
CRAWLER_HTTP_TIMEOUT_SECONDS = float(os.environ.get("CRAWLER_HTTP_TIMEOUT_SECONDS", "30"))
RAW_FRAGMENT_MAX_BYTES = int(os.environ.get("RAW_FRAGMENT_MAX_BYTES", "65536"))
ARCHIVE_RETENTION_DAYS = int(os.environ.get("ARCHIVE_RETENTION_DAYS", "180"))

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULE = {
    "crawl-hot-hourly": {
        "task": "crawler.tasks.dispatch_tier",
        "schedule": 3600.0,
        "args": ("hot",),
    },
    "crawl-warm-six-hourly": {
        "task": "crawler.tasks.dispatch_tier",
        "schedule": 21600.0,
        "args": ("warm",),
    },
    "crawl-cold-daily": {
        "task": "crawler.tasks.dispatch_tier",
        "schedule": 86400.0,
        "args": ("cold",),
    },
    "crawl-discovery-daily": {
        "task": "crawler.tasks.dispatch_tier",
        "schedule": 86400.0,
        "args": ("discovery",),
    },
    "crawl-dormant-weekly": {
        "task": "crawler.tasks.dispatch_tier",
        "schedule": 604800.0,
        "args": ("dormant",),
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "crawler.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
