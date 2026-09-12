# =============================================================================
# Sovereign On-Premise Agentic AI Workbench — SIH 2026 PS 26117 (MRPL)
#
#   make bootstrap    one-time setup: deps, models, infra, database, seed data
#   make dev          run everything (infra + api + worker + web)
#   make test         full test suite (no models required — uses mock provider)
# =============================================================================
SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help bootstrap deps models infra infra-down dev dev-api dev-web \
        db-upgrade db-revision db-reset seed seed-corpus test test-unit test-integration \
        test-docker lint fmt types boundaries check types-gen eval demo sandbox-image \
        airgap clean nuke

UV      := uv
PNPM    := pnpm
API_DIR := apps/api
WEB_DIR := apps/web
COMPOSE := docker compose

# ---------------------------------------------------------------------- help
help:
	@echo "Sovereign AI Workbench — targets:"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------- bootstrap
bootstrap: deps infra db-upgrade seed sandbox-image ## One-time full setup
	@echo
	@echo "Bootstrap complete. Pull models with 'make models', then 'make dev'."

deps: ## Install Python and Node dependencies
	$(UV) sync --all-packages --dev
	$(PNPM) install --frozen-lockfile || $(PNPM) install

models: ## Pull the open-weight models named in config/models.yaml
	bash scripts/pull_models.sh

models-check: ## Report which manifest models are present, pull nothing
	bash scripts/pull_models.sh --check

# --------------------------------------------------------------- infrastructure
infra: ## Start postgres, qdrant and redis, and wait for health
	$(COMPOSE) up -d
	@bash scripts/wait_for_infra.sh

infra-down: ## Stop infrastructure (data is preserved)
	$(COMPOSE) down

# ----------------------------------------------------------------------- dev
dev: infra ## Run the whole stack (Ctrl-C stops everything)
	@bash scripts/dev.sh

dev-api: ## API only, with reload
	cd $(API_DIR) && $(UV) run uvicorn workbench.main:app --reload --port 8000

dev-web: ## Frontend only
	cd $(WEB_DIR) && $(PNPM) dev

# ------------------------------------------------------------------ database
db-upgrade: ## Apply all migrations
	cd $(API_DIR) && $(UV) run alembic upgrade head

db-revision: ## Autogenerate a migration:  make db-revision m="add widgets"
	cd $(API_DIR) && $(UV) run alembic revision --autogenerate -m "$(m)"

db-reset: ## Drop and rebuild the database (destroys all data)
	@bash scripts/db_reset.sh

seed: ## Create roles, permissions and demo users
	$(UV) run python scripts/seed_db.py

seed-corpus: ## Generate the synthetic MRPL corpus and ingest it
	$(UV) run python scripts/seed_corpus.py

# --------------------------------------------------------------------- tests
test: ## Full suite against the deterministic mock provider (no models needed)
	WORKBENCH_PROVIDER=mock $(UV) run pytest

test-unit:
	WORKBENCH_PROVIDER=mock $(UV) run pytest apps/api/tests/unit

test-integration:
	WORKBENCH_PROVIDER=mock $(UV) run pytest apps/api/tests/integration

test-docker: ## Sandbox tests that need a real Docker daemon
	$(UV) run pytest -m docker

test-ollama: ## Provider contract tests against real local models
	OLLAMA_E2E=1 $(UV) run pytest -m ollama

# ------------------------------------------------------------------- quality
lint: ## Lint Python and TypeScript
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	cd $(WEB_DIR) && $(PNPM) lint

fmt: ## Auto-format everything
	$(UV) run ruff check --fix .
	$(UV) run ruff format .
	cd $(WEB_DIR) && $(PNPM) exec prettier --write "src/**/*.{ts,tsx,css}"

types: ## Type-check Python and TypeScript
	$(UV) run mypy apps/api/src
	cd $(WEB_DIR) && $(PNPM) exec tsc --noEmit

boundaries: ## Enforce the module dependency direction
	$(UV) run lint-imports

types-gen: ## Regenerate packages/api-types from the live OpenAPI schema
	$(UV) run python scripts/export_openapi.py > packages/api-types/openapi.json
	cd packages/api-types && $(PNPM) run generate

check: lint types boundaries test ## Everything CI runs

# ------------------------------------------------------------- evals and demo
eval: ## Run every eval suite and print the metrics table
	$(UV) run python -m evals.harness.runner --all

eval-%: ## Run one suite, e.g. make eval-retrieval
	$(UV) run python -m evals.harness.runner --suite $*

demo: ## Scripted end-to-end walkthrough
	$(UV) run python scripts/demo.py

# ----------------------------------------------------------------- packaging
sandbox-image: ## Build the no-network code execution image
	docker build -t workbench/sandbox:0.1.0 services/sandbox

airgap: ## Produce a single offline installation bundle
	bash scripts/airgap_bundle.sh

# ------------------------------------------------------------------- cleanup
clean: ## Remove build and cache artifacts
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache \
	  -o -name .ruff_cache -o -name .next \) -prune -exec rm -rf {} + 2>/dev/null || true

nuke: infra-down clean ## Also delete all runtime data (databases, indexes, blobs)
	rm -rf data/runtime/* && touch data/runtime/.gitkeep
