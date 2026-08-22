PYTHON ?= python3
ENV = DJANGO_SETTINGS_MODULE=billing.settings DJANGO_SECRET_KEY=check-django-secret CLICK_SIGNING_KEY=check-click-secret PRIVACY_HASH_KEY=check-privacy-secret INTERNAL_API_TOKEN=check-api-token BILLING_REDIS_URL=redis://unused

.PHONY: install format lint typecheck test openapi check-openapi check-contracts check-django check-migrations check migrate

install:
	$(PYTHON) -m pip install -r requirements.txt

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .

typecheck:
	$(ENV) $(PYTHON) -m mypy src tests

test:
	$(ENV) $(PYTHON) -m pytest

openapi:
	$(ENV) $(PYTHON) manage.py export_openapi

check-openapi:
	$(ENV) PYTHONPATH=src $(PYTHON) scripts/check_openapi.py

check-contracts:
	$(PYTHON) scripts/check_contracts.py

check-django:
	$(ENV) $(PYTHON) manage.py check

check-migrations:
	$(ENV) $(PYTHON) manage.py makemigrations --check --dry-run

migrate:
	$(ENV) $(PYTHON) manage.py migrate

check: lint typecheck test check-contracts check-openapi check-django check-migrations
