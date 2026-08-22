PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYTHON_FILES = src tests scripts docker manage.py gunicorn.conf.py

.PHONY: install format lint typecheck test publish-openapi check-openapi check-contracts check migrate

install:
	$(PYTHON) -m pip install -r requirements.txt

format:
	$(PYTHON) -m ruff format $(PYTHON_FILES)
	$(PYTHON) -m ruff check --fix $(PYTHON_FILES)

lint:
	$(PYTHON) -m ruff format --check $(PYTHON_FILES)
	$(PYTHON) -m ruff check $(PYTHON_FILES)

typecheck:
	$(PYTHON) -m mypy $(PYTHON_FILES)

test:
	$(PYTHON) -m pytest

migrate:
	$(PYTHON) manage.py migrate --noinput

publish-openapi:
	$(PYTHON) manage.py publish_openapi

check-openapi:
	$(PYTHON) manage.py publish_openapi --check

check-contracts:
	$(PYTHON) scripts/check_contracts.py

check: lint typecheck test check-openapi check-contracts
