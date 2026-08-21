FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

RUN apt-get update \
    && apt-get install --yes --no-install-recommends make curl \
    && addgroup --system crawler \
    && adduser --system --ingroup crawler crawler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./

FROM base AS dependencies
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

FROM dependencies AS runtime
COPY --chown=crawler:crawler . .
RUN chown crawler:crawler /app
USER crawler
EXPOSE 8000
CMD ["gunicorn", "crawler.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-"]

FROM dependencies AS test-dependencies
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-dev.txt

FROM test-dependencies AS test
COPY --chown=crawler:crawler . .
RUN chown crawler:crawler /app
USER crawler
CMD ["make", "check"]
