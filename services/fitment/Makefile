.PHONY: install format lint typecheck test django-check check-contracts check compose-config up down

VENV_BIN := $(if $(wildcard .venv/bin/python),.venv/bin/,)
PYTHON := $(VENV_BIN)python
INFRA_COMPOSE ?= ../../platform/docker-compose.infra.yml
INFRA_SERVICES ?= postgres redis kafka

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

format:
	$(VENV_BIN)ruff format .
	$(VENV_BIN)ruff check --fix .

lint:
	$(VENV_BIN)ruff format --check .
	$(VENV_BIN)ruff check .

typecheck:
	$(VENV_BIN)mypy src tests

test:
	$(VENV_BIN)pytest

django-check:
	$(PYTHON) manage.py check
	$(PYTHON) manage.py makemigrations --check --dry-run

check-contracts:
	$(PYTHON) scripts/check_contracts.py

compose-config:
	FITMENT_DB_PASSWORD=check REDIS_PASSWORD=check docker compose -f docker-compose.yml config --quiet

check: lint typecheck django-check check-contracts test compose-config

up:
	FITMENT_BUILD_CONTEXT=$(CURDIR) docker compose -f $(INFRA_COMPOSE) -f docker-compose.yml up --build $(INFRA_SERVICES) fitment fitment-offers-consumer fitment-decisions-consumer fitment-outbox

down:
	docker compose -f $(INFRA_COMPOSE) -f docker-compose.yml down
