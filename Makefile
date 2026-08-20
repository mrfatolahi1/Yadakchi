# ===========================================================================
# yadakchi — root Makefile
#
# Everything a human needs to run the platform, and everything CI runs.
# `make help` lists the targets. `make ci` is the whole gate, locally.
# ===========================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

REPO_ROOT   := $(shell pwd)
ENV_FILE    := platform/.env
ENV_EXAMPLE := platform/.env.example
INFRA_FILE  := platform/docker-compose.infra.yml
ROOT_FILE   := docker-compose.yml
NETWORK     := yadakchi
VENV        := .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
SCRIPTS     := platform/scripts

COMPOSE       := docker compose --env-file $(ENV_FILE)
INFRA_COMPOSE := $(COMPOSE) -f $(INFRA_FILE)
FULL_COMPOSE  := $(COMPOSE) -f $(ROOT_FILE)

SERVICES := ai crawler enricher fitment matcher catalog search billing ops web
DB_SERVICES := crawler enricher fitment matcher catalog search billing ops

# The long-running infrastructure. The init containers (kafka-data-perms,
# kafka-init, minio-init) do their job and exit 0, so they are not part of the
# health wait — `docker compose up --wait` treats any exit as a failure.
INFRA_HEALTH := postgres kafka kafka-ui kafka-exporter redis minio typesense prometheus grafana

# Services whose agent has actually delivered a Dockerfile. `make up` starts
# these and ignores the rest, so the system is runnable while it is still
# being built.
BUILT_SERVICES = $(strip $(foreach s,$(SERVICES),$(if $(wildcard services/$(s)/Dockerfile),$(s),)))

.PHONY: help
help: ## Show this help
	@echo "yadakchi — platform targets"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  First run:  make env && make infra-up && make topics"

# --------------------------------------------------------------- environment
$(ENV_FILE):
	@cp $(ENV_EXAMPLE) $(ENV_FILE)
	@echo "created $(ENV_FILE) from the example — change every password before leaving your laptop"

.PHONY: env
env: $(ENV_FILE) ## Create platform/.env from the example if it does not exist

.PHONY: net
net: ## Create the shared external docker network
	@docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK)
	@echo "network $(NETWORK) ready"

$(VENV): platform/requirements.txt
	@python3 -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --quiet -r platform/requirements.txt
	@touch $(VENV)
	@echo "virtualenv ready: $(VENV)"

.PHONY: venv
venv: $(VENV) ## Build the platform virtualenv (tooling and tests only)

# ------------------------------------------------------------- infrastructure
.PHONY: infra-up
infra-up: env net ## Start the shared infrastructure and wait for health
	$(INFRA_COMPOSE) up -d
	$(INFRA_COMPOSE) up -d --wait --wait-timeout 300 $(INFRA_HEALTH)
	@# The init containers create the topics and the bucket, then exit. Waiting
	@# for them here means `make topics` right after this cannot race them.
	@for c in yadakchi-kafka-init yadakchi-minio-init; do \
		code=$$(docker wait $$c 2>/dev/null || echo 0); \
		if [ "$$code" != "0" ]; then \
			echo "$$c failed (exit $$code):"; docker logs --tail 30 $$c; exit 1; \
		fi; \
	done
	@echo "topics and buckets provisioned" 
	@echo
	@$(MAKE) --no-print-directory ps
	@echo
	@echo "Kafka UI  http://localhost:$${KAFKA_UI_PORT:-8080}"
	@echo "Grafana   http://localhost:$${GRAFANA_PORT:-3001}"
	@echo "MinIO     http://localhost:$${MINIO_CONSOLE_PORT:-9001}"

.PHONY: infra-down
infra-down: env ## Stop the shared infrastructure (volumes are kept)
	$(INFRA_COMPOSE) down --remove-orphans

.PHONY: infra-logs
infra-logs: env ## Tail infrastructure logs
	$(INFRA_COMPOSE) logs -f --tail=100

# -------------------------------------------------------------- whole system
.PHONY: up
up: env net ## Start infrastructure plus every service that has a Dockerfile
	@if [ -z "$(BUILT_SERVICES)" ]; then \
		echo "No service has a Dockerfile yet — starting infrastructure only."; \
		$(MAKE) --no-print-directory infra-up; \
	else \
		echo "Starting infrastructure + $(BUILT_SERVICES)"; \
		$(FULL_COMPOSE) up -d --build --wait $(BUILT_SERVICES); \
	fi

.PHONY: down
down: env ## Stop everything (volumes are kept)
	$(FULL_COMPOSE) down --remove-orphans

.PHONY: ps
ps: env ## Show containers with their memory limits
	@docker ps --filter "name=yadakchi-" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

.PHONY: stats
stats: ## One-shot docker stats for the yadakchi containers (memory limits)
	@docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}' \
		$$(docker ps --filter "name=yadakchi-" --format '{{.Names}}')

.PHONY: clean
clean: env ## Stop everything and DELETE all data volumes
	@read -p "This deletes every database, topic and archived page. Type yes: " ok; \
	[ "$$ok" = "yes" ] || { echo "aborted"; exit 1; }
	$(FULL_COMPOSE) down -v --remove-orphans

# --------------------------------------------------------------------- kafka
.PHONY: topics
topics: env ## Apply platform/kafka/topics.yml to the broker (idempotent)
	@COMPOSE_FILE=$(INFRA_FILE) bash platform/kafka/create_topics.sh

.PHONY: topics-list
topics-list: env ## List the topics the broker actually has
	@$(INFRA_COMPOSE) exec -T kafka /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server localhost:9092 --list | sort

.PHONY: topics-describe
topics-describe: env ## Describe every topic (partitions, configs)
	@$(INFRA_COMPOSE) exec -T kafka /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server localhost:9092 --describe

# ------------------------------------------------------------------ postgres
.PHONY: psql
psql: env ## Shell into a service database: make psql svc=matcher
	@test -n "$(svc)" || { echo "usage: make psql svc=<$(DB_SERVICES)>"; exit 2; }
	@echo "$(DB_SERVICES)" | tr ' ' '\n' | grep -qx "$(svc)" \
		|| { echo "'$(svc)' has no database. One of: $(DB_SERVICES)"; exit 2; }
	@set -a; . ./$(ENV_FILE); set +a; \
	PW_VAR=$$(echo $(svc) | tr 'a-z' 'A-Z')_DB_PASSWORD; \
	$(INFRA_COMPOSE) exec -e PGPASSWORD="$${!PW_VAR}" -T postgres \
		psql -U $(svc) -d yadakchi_$(svc) $(if $(c),-c "$(c)",) \
		|| { echo; echo "(interactive shells: docker exec -it yadakchi-postgres psql -U $(svc) -d yadakchi_$(svc))"; exit 1; }

.PHONY: psql-shell
psql-shell: env ## Interactive psql for a service: make psql-shell svc=matcher
	@test -n "$(svc)" || { echo "usage: make psql-shell svc=<$(DB_SERVICES)>"; exit 2; }
	@set -a; . ./$(ENV_FILE); set +a; \
	PW_VAR=$$(echo $(svc) | tr 'a-z' 'A-Z')_DB_PASSWORD; \
	docker exec -it -e PGPASSWORD="$${!PW_VAR}" yadakchi-postgres psql -U $(svc) -d yadakchi_$(svc)

# ------------------------------------------------------------------ contracts
.PHONY: check-contracts
check-contracts: $(VENV) ## Fail if any consumed/ schema has drifted from its publisher
	@$(PY) $(SCRIPTS)/check_contracts.py

.PHONY: sync-contracts
sync-contracts: $(VENV) ## Copy published schemas into every declared consumer
	@$(PY) $(SCRIPTS)/sync_contracts.py

# ---------------------------------------------------------------------- specs
.PHONY: sync-specs
sync-specs: $(VENV) ## Distribute docs/specs into the service folders (idempotent)
	@$(PY) $(SCRIPTS)/sync_specs.py

.PHONY: check-specs
check-specs: $(VENV) ## Fail if a distributed BRIEF.md or SPEC.md has drifted
	@$(PY) $(SCRIPTS)/check_specs.py

# ------------------------------------------------------------------- quality
.PHONY: lint
lint: $(VENV) ## ruff check + ruff format --check (platform only)
	@$(VENV)/bin/ruff check platform
	@$(VENV)/bin/ruff format --check platform

.PHONY: format
format: $(VENV) ## Reformat the platform's Python
	@$(VENV)/bin/ruff format platform
	@$(VENV)/bin/ruff check --fix platform

.PHONY: typecheck
typecheck: $(VENV) ## mypy --strict (platform only)
	@$(VENV)/bin/mypy

.PHONY: test
test: $(VENV) ## Platform unit tests (no containers needed)
	@$(PY) -m pytest -m "not infra"

.PHONY: verify
verify: $(VENV) ## Acceptance tests against running infrastructure
	@$(PY) -m pytest -m infra -v

.PHONY: compose-config
compose-config: env ## Validate that both compose files render
	@docker compose --env-file $(ENV_FILE) -f $(INFRA_FILE) config -q && echo "infra compose OK"
	@docker compose --env-file $(ENV_FILE) -f $(ROOT_FILE) config -q && echo "root compose OK"

# ------------------------------------------------------------------------- ci
.PHONY: ci
ci: ## Everything CI gates on, run locally
	@set -e; \
	$(MAKE) --no-print-directory check-specs; \
	$(MAKE) --no-print-directory check-contracts; \
	$(MAKE) --no-print-directory lint; \
	$(MAKE) --no-print-directory typecheck; \
	$(MAKE) --no-print-directory test; \
	if docker info >/dev/null 2>&1; then \
		$(MAKE) --no-print-directory compose-config; \
	else \
		echo "SKIP  compose-config: docker is not available on this machine"; \
	fi; \
	echo; echo "ci: all checks passed"
