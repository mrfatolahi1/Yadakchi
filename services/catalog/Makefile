# ===========================================================================
# catalog — everything this service needs, from one directory.
#
#   make check    # what CI gates on: lint, types, contracts, tests
#   make up       # the service on the shared infrastructure
#   make test     # tests in a container, against a throwaway Postgres
# ===========================================================================

SHELL       := /bin/bash
.DEFAULT_GOAL := help

REPO_ROOT   := ../..
ENV_FILE    := $(REPO_ROOT)/platform/.env
COMPOSE     := docker compose --env-file $(ENV_FILE) -f docker-compose.yml
VENV        := .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
export PYTHONPATH := src
export DJANGO_SETTINGS_MODULE := catalog.settings

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

$(VENV): requirements-dev.txt requirements.txt
	@python3 -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --quiet -r requirements-dev.txt
	@touch $(VENV)

.PHONY: venv
venv: $(VENV) ## Build the local virtualenv

# ------------------------------------------------------------------ quality
.PHONY: lint
lint: $(VENV) ## ruff check + ruff format --check
	@$(VENV)/bin/ruff check src tests
	@$(VENV)/bin/ruff format --check src tests

.PHONY: format
format: $(VENV) ## Reformat and autofix
	@$(VENV)/bin/ruff format src tests
	@$(VENV)/bin/ruff check --fix src tests

.PHONY: typecheck
typecheck: $(VENV) ## mypy --strict
	@$(VENV)/bin/mypy

# --------------------------------------------------------------------- test
# Tests need a real Postgres: this service uses array columns, JSONB and a
# range-partitioned table, none of which SQLite can stand in for.
# NOTE: never pass --remove-orphans here. platform/.env sets
# COMPOSE_PROJECT_NAME=yadakchi, so every service in the monorepo shares one
# compose project, and "orphans" from this file's point of view are other
# teams' running containers. Only ever stop what this file declares.
.PHONY: test
test: ## Run the test suite in a container (no local Postgres needed)
	@docker network inspect yadakchi >/dev/null 2>&1 || docker network create yadakchi
	@$(COMPOSE) --profile test build --quiet catalog-tests
	@$(COMPOSE) --profile test run --rm -T catalog-tests pytest $(ARGS)
	@$(COMPOSE) --profile test stop catalog-test-db >/dev/null 2>&1 || true
	@$(COMPOSE) --profile test rm -f catalog-test-db >/dev/null 2>&1 || true

.PHONY: test-local
test-local: $(VENV) ## Run the tests against $$DATABASE_URL directly
	@CATALOG_EVENT_TRANSPORT=memory $(VENV)/bin/pytest $(ARGS)

# ---------------------------------------------------------------- contracts
.PHONY: openapi
openapi: $(VENV) ## Regenerate contracts/published/openapi.json
	@$(PY) manage.py export_openapi

.PHONY: check-openapi
check-openapi: $(VENV) ## Fail if openapi.json is out of date
	@$(PY) manage.py export_openapi --check

.PHONY: check-contracts
check-contracts: ## Run the repo's contract drift guard
	@$(MAKE) --no-print-directory -C $(REPO_ROOT) check-contracts

# ----------------------------------------------------------------------- ci
.PHONY: check
check: ## Everything CI gates on, locally
	@set -e; \
	$(MAKE) --no-print-directory lint; \
	$(MAKE) --no-print-directory typecheck; \
	$(MAKE) --no-print-directory check-openapi; \
	$(MAKE) --no-print-directory check-contracts; \
	$(MAKE) --no-print-directory test; \
	echo; echo "catalog: all checks passed"

# ------------------------------------------------------------------ runtime
.PHONY: up
up: ## Start the service on the shared infrastructure
	@docker network inspect yadakchi >/dev/null 2>&1 || docker network create yadakchi
	@$(COMPOSE) up -d --build

.PHONY: down
down: ## Stop this service (infrastructure and other services keep running)
	@$(COMPOSE) stop catalog catalog-clusters catalog-offers catalog-fitments \
		catalog-vehicles catalog-crossrefs catalog-clicks catalog-worker catalog-beat
	@$(COMPOSE) rm -f catalog catalog-clusters catalog-offers catalog-fitments \
		catalog-vehicles catalog-crossrefs catalog-clicks catalog-worker catalog-beat

.PHONY: logs
logs: ## Tail this service's logs
	@$(COMPOSE) logs -f --tail=100

.PHONY: migrate
migrate: ## Apply migrations inside the running container
	@$(COMPOSE) exec catalog python manage.py migrate --noinput

.PHONY: rebuild-all
rebuild-all: ## Recompute every product from the local read models
	@$(COMPOSE) exec catalog python manage.py rebuild_all --emit

.PHONY: shell
shell: ## Django shell inside the running container
	@$(COMPOSE) exec catalog python manage.py shell
