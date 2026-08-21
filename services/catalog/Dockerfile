# ---------------------------------------------------------------------------
# catalog — Django 5 + Django Ninja + Celery
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    DJANGO_SETTINGS_MODULE=catalog.settings

# librdkafka is what confluent-kafka binds to; curl is the healthcheck.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY manage.py pyproject.toml ./
COPY src ./src
COPY contracts ./contracts

RUN useradd --create-home --uid 10001 catalog \
    && mkdir -p /app/staticfiles \
    && chown -R catalog:catalog /app
USER catalog

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8000/v1/health || exit 1

CMD ["gunicorn", "catalog.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]

# ---------------------------------------------------------------------------
# Test image: the same code plus the dev toolchain.
# ---------------------------------------------------------------------------
FROM base AS test
USER root
RUN pip install -r requirements-dev.txt
COPY tests ./tests
RUN chown -R catalog:catalog /app
USER catalog
CMD ["pytest"]
