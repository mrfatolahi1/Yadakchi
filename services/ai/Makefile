# ===========================================================================
# ai — service targets.
#
#   make check        everything CI gates on: lint, types, tests, contract
#   make up           this service plus Redis, nothing else
#   make docker-test  the suite inside the image
#
# `make check` needs no containers and no network: the default backend is the
# stub. That is the point — nine other services depend on it.
# ===========================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV      := .venv
PY        := $(VENV)/bin/python
PIP       := $(VENV)/bin/pip
RUFF      := $(VENV)/bin/ruff
MYPY      := $(VENV)/bin/mypy
PYTEST    := $(PY) -m pytest

REPO_ROOT := ../..
ENV_FILE  := $(REPO_ROOT)/platform/.env
COMPOSE   := docker compose --env-file $(ENV_FILE) -f docker-compose.yml
NETWORK   := yadakchi
PORT      := $(shell grep -E '^AI_PORT=' $(ENV_FILE) 2>/dev/null | cut -d= -f2)
PORT      := $(if $(PORT),$(PORT),8003)

.PHONY: help
help: ## Show this help
	@echo "ai — service targets"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------- toolchain
$(VENV): requirements-dev.txt requirements.txt
	@python3 -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --quiet -r requirements-dev.txt
	@touch $(VENV)
	@echo "virtualenv ready: $(VENV)"

.PHONY: install
install: $(VENV) ## Create the virtualenv and install dev dependencies

.PHONY: install-embed
install-embed: $(VENV) ## Also install sentence-transformers (torch, ~2 GB)
	@$(PIP) install -r requirements-embed.txt

# ------------------------------------------------------------------ quality
.PHONY: lint
lint: $(VENV) ## ruff check + ruff format --check
	@$(RUFF) check .
	@$(RUFF) format --check .

.PHONY: format
format: $(VENV) ## Reformat and apply safe fixes
	@$(RUFF) format .
	@$(RUFF) check --fix .

.PHONY: typecheck
typecheck: $(VENV) ## mypy --strict
	@$(MYPY) .

.PHONY: test
test: $(VENV) ## Run the suite (offline, stub backend)
	@$(PYTEST)

.PHONY: check
check: $(VENV) ## Everything CI gates on, in CI's order
	@set -e; \
	echo "== ruff check"; $(RUFF) check .; \
	echo "== ruff format --check"; $(RUFF) format --check .; \
	echo "== mypy"; $(MYPY) .; \
	echo "== pytest"; $(PYTEST); \
	if docker info >/dev/null 2>&1 && [ -f $(ENV_FILE) ]; then \
		echo "== compose config"; $(COMPOSE) config -q && echo "compose OK"; \
	else \
		echo "SKIP  compose config: docker or $(ENV_FILE) unavailable"; \
	fi; \
	echo; echo "ai: all checks passed"

# ----------------------------------------------------------------- contract
.PHONY: openapi
openapi: $(VENV) ## Regenerate contracts/published/openapi.json
	@PYTHONPATH=src $(PY) -m ai.export_openapi

.PHONY: prompt-fingerprint
prompt-fingerprint: $(VENV) ## Stamp the prompt digest after a deliberate change
	@PYTHONPATH=src $(PY) -m ai.prompts --write
	@echo "now decide whether PROMPT_VERSION should move too (it invalidates the cache)"

# ---------------------------------------------------------------------- run
.PHONY: run
run: $(VENV) ## Run locally with reload on http://127.0.0.1:8000
	@PYTHONPATH=src $(VENV)/bin/uvicorn ai.main:app --reload --host 127.0.0.1 --port 8000

# ------------------------------------------------------------------- docker
$(ENV_FILE):
	@$(MAKE) -C $(REPO_ROOT) env

.PHONY: net
net: ## Create the shared external docker network
	@docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK)

.PHONY: up
up: $(ENV_FILE) net ## Build and start this service plus Redis
	@$(COMPOSE) up -d --build --wait ai
	@echo "ai is up: http://127.0.0.1:$(PORT)/health"

.PHONY: down
down: $(ENV_FILE) ## Stop this service (Redis and the rest of the infra stay up)
	@$(COMPOSE) stop ai
	@$(COMPOSE) rm -f ai

.PHONY: logs
logs: $(ENV_FILE) ## Tail this service's logs
	@$(COMPOSE) logs -f --tail=100 ai

.PHONY: smoke
smoke: $(ENV_FILE) ## Call every endpoint of the running container
	@set -e; \
	base=http://127.0.0.1:$(PORT); \
	echo "== /health";  curl -sf $$base/health | head -c 400; echo; \
	echo "== /v1/extract"; curl -sf -X POST $$base/v1/extract \
		-H 'Content-Type: application/json' \
		-d '{"text":"فیلتر روغن پژو 206 اصلی ایساکو کد 1109AY","schema_name":"offer_fields"}' \
		| head -c 400; echo; \
	echo "== /v1/judge"; curl -sf -X POST $$base/v1/judge \
		-H 'Content-Type: application/json' \
		-d '{"a":"لنت ترمز جلو پراید اصلی سایپا یدک","b":"لنت ترمز جلو پراید تخت جمشید"}' \
		| head -c 400; echo; \
	echo "== /v1/embed"; curl -sf -X POST $$base/v1/embed \
		-H 'Content-Type: application/json' -d '{"texts":["لنت ترمز پراید"]}' \
		| head -c 120; echo; \
	echo "== /metrics"; curl -sf $$base/metrics | grep -c yadakchi_ai

.PHONY: docker-test
docker-test: $(ENV_FILE) net ## Run the suite inside the image
	@$(COMPOSE) --profile test build ai-tests
	@$(COMPOSE) --profile test run --rm ai-tests
