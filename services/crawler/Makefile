VENV_BIN := $(if $(wildcard .venv/bin/python),.venv/bin/,)
PYTHON ?= $(VENV_BIN)python
PYTEST ?= $(VENV_BIN)pytest
RUFF ?= $(VENV_BIN)ruff
MYPY ?= $(VENV_BIN)mypy

.PHONY: install migrate run worker beat consume-clicks crawl replay test lint format typecheck check-contracts check compose-check up down

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

migrate:
	$(PYTHON) manage.py migrate --noinput

run:
	$(PYTHON) manage.py runserver 0.0.0.0:8000

worker:
	$(VENV_BIN)celery -A crawler worker --loglevel=INFO

beat:
	$(VENV_BIN)celery -A crawler beat --loglevel=INFO

consume-clicks:
	$(PYTHON) manage.py consume_clicks

crawl:
	$(PYTHON) manage.py crawl --source=$(SOURCE) --tier=$(or $(TIER),discovery)

replay:
	$(PYTHON) manage.py replay_archive --source=$(SOURCE) $(ARGS)

test:
	$(PYTEST) -q

lint:
	$(RUFF) check src tests manage.py scripts

format:
	$(RUFF) format src tests manage.py scripts

typecheck:
	$(MYPY) src tests scripts

check-contracts:
	$(PYTHON) scripts/check_contracts.py

check: check-contracts
	$(RUFF) format --check src tests manage.py scripts
	$(RUFF) check src tests manage.py scripts
	$(MYPY) src tests scripts
	$(PYTHON) manage.py check
	$(PYTHON) manage.py makemigrations --check --dry-run
	$(PYTEST) -q

compose-check:
	docker compose --profile check run --rm --build crawler-check

up:
	docker compose up --build

down:
	docker compose down
