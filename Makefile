# =============================================================================
# Makefile — Enterprise AI-Driven Data Quality & Cataloging Agent
#
# Development workflow targets for local sandbox, testing, and Day 1/2 ops.
#
# DEPENDENCY MANAGEMENT: uv (Astral) — https://docs.astral.sh/uv/
# =============================================================================

.PHONY: help local-up local-down local-status migrate migrate-down \
        test test-unit test-integration test-coverage shell clean \
        install lint format typecheck build

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Local Sandbox ────────────────────────────────────────────────────────────

local-up: ## Start local dev stack (Postgres+pgvector+LocalStack)
	cd local_development && docker compose up -d
	@echo "Waiting for services to become healthy..."
	@until docker compose -f local_development/docker-compose.yml exec -T postgres pg_isready -d postgres > /dev/null 2>&1; do sleep 2; done
	@echo "PostgreSQL is ready."
	@until curl -sf http://localhost:4566/_localstack/health | grep -q s3; do sleep 2; done
	@echo "LocalStack is ready."

local-down: ## Stop and remove local dev stack
	cd local_development && docker compose down

local-down-clean: ## Stop, remove volumes, reset everything
	cd local_development && docker compose down -v

local-status: ## Show status of local dev services
	cd local_development && docker compose ps

local-logs: ## Tail logs from all local services
	cd local_development && docker compose logs -f

local-db: ## Open psql shell on local postgres
	docker exec -it ai-catalog-pgvector psql -U postgres -d postgres

local-s3: ## List S3 buckets in LocalStack
	docker exec -it ai-catalog-localstack awslocal s3 ls

# ── Dependencies ─────────────────────────────────────────────────────────────

install: ## Install Python dependencies via uv (includes dev dependencies)
	uv sync

lock: ## Resolve and lock dependencies (updates uv.lock)
	uv lock

lock-check: ## Verify uv.lock is up to date with pyproject.toml
	uv lock --check

shell: ## Activate the uv-managed virtual environment
	@echo "Run: source .venv/bin/activate  (macOS/Linux)"
	@echo "Run: .venv\\Scripts\\Activate.ps1  (Windows PowerShell)"
	@echo "Or prefix commands with 'uv run <cmd>'"

# ── Database Migrations ──────────────────────────────────────────────────────

migrate: ## Run all pending Alembic migrations
	uv run alembic upgrade head

migrate-down: ## Roll back the last migration
	uv run alembic downgrade -1

migrate-reset: ## Roll back all migrations and re-apply
	uv run alembic downgrade base
	uv run alembic upgrade head

migrate-new: ## Create a new migration revision (usage: make migrate-new MSG="description")
	uv run alembic revision --autogenerate -m "$(MSG)"

migrate-history: ## Show migration history
	uv run alembic history

migrate-current: ## Show current migration version
	uv run alembic current

# ── Testing ──────────────────────────────────────────────────────────────────

test: ## Run all tests
	uv run pytest -v

test-unit: ## Run only unit tests
	uv run pytest tests/unit/ -v

test-integration: ## Run only integration tests
	uv run pytest tests/integration/ -v

test-coverage: ## Run tests with coverage report
	uv run pytest --cov=src --cov-report=term-missing --cov-report=html

# ── Code Quality ─────────────────────────────────────────────────────────────

lint: ## Run ruff linter
	uv run ruff check src/ tests/

format: ## Run ruff formatter
	uv run ruff format src/ tests/

typecheck: ## Run mypy type checker
	uv run mypy src/

# ── Container ────────────────────────────────────────────────────────────────

build: ## Build the Docker image
	docker build -t ai-catalog-agent:local -f Dockerfile .

# ── Cleanup ──────────────────────────────────────────────────────────────────

clean: ## Clean Python cache and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	rm -rf .coverage htmlcov/ dist/ build/ *.egg-info