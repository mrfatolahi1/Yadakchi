# ===========================================================================
# ai — FastAPI inference service.
#
# The default image is deliberately small and offline-capable: AI_BACKEND=stub
# needs no weights and no network, which is what lets the other nine services
# run their suites against a container of this one.
#
# Production, with the locally loaded 384-dimension embedder:
#
#   docker build --build-arg WITH_LOCAL_EMBEDDINGS=1 -t yadakchi-ai .
#
# That variant installs sentence-transformers (torch, ~2 GB) and bakes the
# model weights into /opt/models at build time, so the running container never
# needs to reach the internet.
# ===========================================================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    SENTENCE_TRANSFORMERS_HOME=/opt/models \
    HF_HOME=/opt/models \
    TZ=UTC

WORKDIR /app

ARG WITH_LOCAL_EMBEDDINGS=0
ARG AI_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

COPY requirements.txt requirements-embed.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_LOCAL_EMBEDDINGS" = "1" ]; then \
        python -m pip install --no-cache-dir -r requirements-embed.txt \
        && mkdir -p /opt/models \
        && python -c "from sentence_transformers import SentenceTransformer as S; S('${AI_EMBED_MODEL}')" ; \
    fi

COPY src ./src
COPY contracts ./contracts

# Runs as a normal user: this process talks to a model provider and to Redis,
# and needs nothing that root has.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app /opt/models 2>/dev/null || true
USER app

# --------------------------------------------------------------------- tests
# `make docker-test` runs the suite exactly as CI does, inside the image and
# with no network. Declared before the runtime stage on purpose: a build with
# no --target builds the *last* stage, and that must be the one we ship.
FROM base AS test

USER root
COPY requirements-dev.txt ./
RUN python -m pip install --no-cache-dir -r requirements-dev.txt
COPY pyproject.toml ./
COPY tests ./tests
RUN chown -R app:app /app
USER app

CMD ["python", "-m", "pytest", "-q"]


# ------------------------------------------------------------------- runtime
FROM base AS runtime

EXPOSE 8000

# python rather than curl: the slim image has no curl and this needs no apt.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# One worker per container. Concurrency is capped inside the process
# (AI_MAX_CONCURRENCY) because the model, not the event loop, is the
# bottleneck; scale by adding containers, all sharing the Redis counters.
#
# Access logging stays on: uvicorn's line goes through this service's JSON
# formatter and carries a method, a path and a status — no request body, so no
# titles. Turn it off with --no-access-log if the volume ever justifies it.
CMD ["uvicorn", "ai.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
