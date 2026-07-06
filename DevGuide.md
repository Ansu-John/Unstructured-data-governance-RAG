# Development Guide — Enterprise AI Data Catalog Agent

## Local Environment Setup

### Prerequisites

- Docker Desktop 4.25+ with at least 4 GB allocated memory
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 0.5.x (`pip install uv` or use the standalone installer)
- AWS CLI (for LocalStack interop; not required if using only Docker)
- Make (included on macOS/Linux; **optional on Windows** — see "Windows Users" note below)

### First-Time Setup

The project includes a `Makefile` for convenience. **On macOS/Linux**, use `make <target>`. **On Windows**, run the equivalent PowerShell commands directly.

<details>
<summary><b>macOS / Linux (with make)</b></summary>

```bash
# 1. Copy environment template and customize if needed
cp .env.example .env

# 2. Start the local development stack
make local-up
#   Starts: PostgreSQL 16 + pgvector (port 5433)
#          LocalStack S3 mock (port 4566) with Medallion buckets
#   Init scripts run automatically (01-init.sql, 02-init-s3-buckets.sh)

# 3. Install Python dependencies (creates .venv automatically)
uv sync

# 4. Activate the virtual environment (optional — uv run works without it)
source .venv/bin/activate

# 5. Run Alembic migrations to create schemas
make migrate

# 6. Verify the stack is healthy
make local-status           # Both containers should show "healthy"
docker exec -it ai-catalog-pgvector psql -U postgres -d postgres -c "\dt catalog.*"
```
</details>

<details>
<summary><b>Windows (PowerShell — no make needed)</b></summary>

```powershell
# 1. Copy environment template and customize if needed
Copy-Item .env.example .env

# 2. Start the local development stack
cd local_development
docker compose up -d
cd ..

# 3. Wait for both services to be healthy
#    (Check with: docker compose -f local_development/docker-compose.yml ps)
#    Both 'postgres' and 'localstack' should show "healthy"

# 4. Install Python dependencies (creates .venv automatically)
uv sync

# 5. Activate the virtual environment (optional — uv run works without it)
.venv\Scripts\Activate.ps1

# 6. Run Alembic migrations to create schemas
uv run alembic upgrade head

# 7. Verify the stack is healthy
docker compose -f local_development/docker-compose.yml ps
docker exec ai-catalog-pgvector psql -U postgres -d postgres -c "\dt catalog.*"
```
</details>

<br>

### Daily Development Workflow

Common tasks are available as `make` targets (macOS/Linux) or manual commands (Windows):

| Task | macOS/Linux (make) | Windows (PowerShell) |
|------|--------------------|----------------------|
| Start stack | `make local-up` | `cd local_development; docker compose up -d` |
| Stop stack | `make local-down` | `cd local_development; docker compose down` |
| Reset stack | `make local-down-clean` | `cd local_development; docker compose down -v` |
| View status | `make local-status` | `docker compose -f local_development/docker-compose.yml ps` |
| Install deps | `uv sync` | `uv sync` |
| Lock deps | `uv lock` | `uv lock` |
| Check lockfile | `uv lock --check` | `uv lock --check` |
| Run linter | `make lint` | `uv run ruff check src/ tests/` |
| Type check | `make typecheck` | `uv run mypy src/` |
| Run all tests | `make test` | `uv run pytest -v` |
| Run unit tests | `make test-unit` | `uv run pytest tests/unit/ -v` |
| Run integration tests | `make test-integration` | `uv run pytest tests/integration/ -v` |
| Run coverage | `make test-coverage` | `uv run pytest --cov=src --cov-report=term-missing` |
| Check DB | `make local-db` | `docker exec -it ai-catalog-pgvector psql -U postgres -d postgres` |
| List S3 buckets | `make local-s3` | `docker exec -it ai-catalog-localstack awslocal s3 ls` |
| View logs | `make local-logs` | `docker compose -f local_development/docker-compose.yml logs -f` |
| Run migrations | `make migrate` | `uv run alembic upgrade head` |
| Rollback migration | `make migrate-down` | `uv run alembic downgrade -1` |

```bash
# Run the full agent graph (any OS)
uv run python -m src.agents.graph_builder

# Run quality validation (any OS)
uv run python -m src.data_pipeline.quality.gx_suites \
    --bronze-path s3://ai-catalog-bronze-dev/crm/users/ \
    --silver-path s3://ai-catalog-silver-dev/crm/users/ \
    --quarantine-path s3://ai-catalog-bronze-dev/_quarantine/ \
    --suite-name crm_users_suite \
    --expectation-threshold 0.95
```

### Accessing Local Services

```bash
# PostgreSQL psql shell
make local-db

# List S3 buckets in LocalStack
make local-s3

# View container logs
make local-logs

# Browse LocalStack health
curl http://localhost:4566/_localstack/health | jq .
```

---

## Architecture Deep Dive

### System Topology

```
┌─────────────────────────────────────────────────────────────┐
│                     LangGraph State Machine                  │
│  (ECS Fargate, compiled with PostgresSaver checkpointer)    │
│                                                              │
│  [ingestion] → [profiling] → [quality_router] ──→ [cataloging]
│       ↑                           ↓                    │     │
│       │                    [log_fail_and_quarantine]   │     │
│       │                           ↓                    │     │
│       └────── retry_router ──────┘                    │     │
│                                                  [advance_file]
│                                                       │     │
│                                        all done ──────┘     │
│                                           next file ──→ [ingestion]
└─────────────────────────────────────────────────────────────┘
```

### State Schema (AgentState TypedDict)

| Key | Type | Description |
|-----|------|-------------|
| `files` | `List[Dict]` | Discovered Bronze files (FileRecord dicts) |
| `current_file_id` | `str` | File currently being processed |
| `quality_results` | `Dict[str, Dict]` | QualityResult per file_id |
| `profile_results` | `Dict[str, Dict]` | ProfileResult per file_id |
| `catalog_entries` | `List[Dict]` | Successful catalog records |
| `retry_count` | `int` | Retry attempts for current file |
| `errors` | `List[str]` | Accumulated error messages |

### Medallion Bucket Structure

```
s3://ai-catalog-bronze-dev/     ← Raw landing zone (immutable)
  └── <source_system>/<object_type>/year=<YYYY>/month=<MM>/day=<DD>/

s3://ai-catalog-silver-dev/     ← Cleaned & validated (Parquet)
  └── <source_system>/<object_type>/year=<YYYY>/month=<MM>/day=<DD>/

s3://ai-catalog-gold-dev/       ← Business-ready (optimized for BI/vector)
  └── <domain>/

s3://.../_quarantine/           ← Failed GX validation rows
  └── <asset_name>/run_id=<UUID>/
```

---

## Enhancing the Application

### Adding a New Expectation Suite

Expectation suites are defined in `src/data_pipeline/quality/gx_suites.py`:

```python
# In the SUITE_REGISTRY dict:
"orders_suite": [
    ("expect_column_values_to_not_be_null", {"column": "order_id"}, {"priority": "critical"}),
    ("expect_column_values_to_be_unique", {"column": "order_id"}, {"priority": "critical"}),
    ("expect_column_values_to_be_between", {"column": "amount", "min_value": 0.01}, {"priority": "high"}),
    ("expect_column_values_to_match_regex", {"column": "currency", "regex": r"^[A-Z]{3}$"}, {"priority": "medium"}),
],
```

**Suite priority convention:**
- **critical** — Must pass or file is quarantined (null check on PK, uniqueness)
- **high** — Major concern but not blocking (type checks, range checks)
- **medium** — Informational (regex patterns, format checks)

**Testing a new suite locally:**

```bash
# After adding the suite to SUITE_REGISTRY, run validation:
uv run python -m src.data_pipeline.quality.gx_suites \
    --bronze-path s3://ai-catalog-bronze-dev/test/orders/ \
    --silver-path s3://ai-catalog-silver-dev/test/orders/ \
    --suite-name orders_suite \
    --expectation-threshold 0.95
```

### Adding a New Graph Node

1. **Create the node file** in `src/agents/nodes/`:

```python
# src/agents/nodes/enrichment.py
from typing import Any, Dict
from src.agents.state import AgentState

try:
    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)
    HAS_OTEL = True
except ImportError:
    tracer = None
    HAS_OTEL = False

def enrichment_node(state: AgentState) -> Dict[str, Any]:
    """Enrich catalog entries with external metadata."""
    span_ctx = tracer.start_as_current_span("enrichment_node") if HAS_OTEL else _NoopSpan()
    with span_ctx as span:
        try:
            # Implementation here
            return {"enrichment_summary": "enriched 5 entries"}
        except Exception as exc:
            span.record_exception(exc) if HAS_OTEL else None
            return {"errors": [f"enrichment_node: {exc}"]}
```

2. **Register the node** in `src/agents/graph_builder.py`:

```python
from src.agents.nodes.enrichment import enrichment_node

workflow.add_node("enrichment", enrichment_node)
workflow.add_edge("cataloging", "enrichment")
```

3. **Add state fields** to `AgentState` in `src/agents/state.py`:

```python
class AgentState(TypedDict, total=False):
    # ... existing fields ...
    enrichment_summary: str
```

4. **Test the new node:**
```bash
uv run pytest tests/ -v -k "enrichment"
```

### Updating / Creating a Terraform Module

```bash
# 1. Create module directory
mkdir -p infrastructure/modules/my_module/

# 2. Create module files
touch infrastructure/modules/my_module/{main.tf,variables.tf,outputs.tf}

# 3. Consume in appropriate lifecycle tier
# In infrastructure/02-platform-medium/dev/main.tf:
module "my_module" {
  source      = "../../modules/my_module"
  environment = local.environment
  tags        = local.tags
}

# 4. Add SSM parameter for cross-tier handoff (if needed)
resource "aws_ssm_parameter" "my_output" {
  name  = "/${local.environment}/platform-medium/my-output"
  type  = "String"
  value = module.my_module.some_output
}

# 5. Format and validate
cd infrastructure/02-platform-medium/dev
terraform fmt
terraform validate

# 6. IMPORTANT: If your module creates infrastructure consumed by CI/CD
#    (e.g., ECR repositories, ECS resources), ensure you add the
#    corresponding infra-check validation in app-python-cicd.yml
#    See .github/workflows/app-python-cicd.yml for the pattern.
```

### Adding a Database Migration

```bash
# 1. Generate a new revision (autogenerate detects SQLAlchemy model changes)
uv run alembic revision --autogenerate -m "add_data_classification"

# 2. Review and edit the generated file
#    File: src/db_migrations/versions/0003_add_data_classification.py

# 3. Apply
uv run alembic upgrade head

# 4. Rollback if needed
uv run alembic downgrade -1

# 5. Commit
git add src/db_migrations/versions/
git commit -m "feat: add data_classification column to catalog.data_assets"
```

> **Rule:** Never edit an applied migration. Always create a new revision.

---

## CI/CD Pipelines

### Infrastructure Pipelines (Tag-Triggered)

| Pipeline | Trigger Tag | Approvals | State Location |
|----------|-------------|-----------|----------------|
| `infra-01-static.yml` | `v*.*.*-core` | Manual apply gate | `s3://tf-state/dev/core-static/` |
| `infra-02-platform.yml` | `v*.*.*-platform` | Manual apply gate | `s3://tf-state/dev/platform-medium/` |
| `infra-03-app-dynamic.yml` | `v*.*.*-app` | Manual apply gate | `s3://tf-state/dev/app-dynamic/` |

### Application Pipeline (Push-Triggered)

**Workflow:** `app-python-cicd.yml`

Trigger: Push to `main` or PR targeting `main`

Stages:
0. **Infrastructure Validation Gate** (NEW) — Checks that ECR repository, ECS cluster, and ECS service exist before proceeding. If infra is missing, the pipeline fails immediately with the exact Terraform command to run. Saves ~10 minutes vs. failing at the Docker push step.
1. **Setup** — `astral-sh/setup-uv@v3` installs uv; `uv sync --frozen` installs dependencies
2. **Lockfile check** — `uv lock --check` verifies `uv.lock` is in sync
3. **Lint** — `uv run ruff check src/ tests/`
4. **Type check** — `uv run mypy src/` (continue-on-error)
5. **Test** — `uv run pytest --cov=src` (starts Docker Compose for integration tests)
6. **Docker build** — Multi-stage Docker build with BuildKit cache; push to ECR
7. **ECR Scan** — Waits for ECR vulnerability scan to complete and reports findings
8. **ECS deploy** — Render task definition, rolling update, circuit breaker, wait for stability

**Key pipeline changes:**
- The `docker-build` job now depends on both `infra-check` and `lint-test` — ensuring infrastructure is validated before spending build minutes
- ECR scan findings are reported after push (non-blocking informational step)
- The deploy step includes error handling for missing task definitions with actionable next steps

### Infrastructure Validation Gate Details

The `infra-check` job validates three resources:

1. **ECR Repository** (`ai-catalog-agent`) — **Hard requirement**. If missing, the pipeline fails immediately.
2. **ECS Cluster** (`dev-ai-catalog-ecs`) — **Informational warning**. May not exist on first deploy.
3. **ECS Service** (`dev-ai-catalog-agent-svc`) — **Informational warning**. Created by Tier 3 Terraform.

To resolve a failed infrastructure check:
```bash
# Apply the 02-platform-medium tier (includes ECR repository)
cd infrastructure/02-platform-medium/dev
terraform init
terraform apply
```

---

## GitHub Workflow: Branch, Code Review, and Merge

### Standard Development Flow

```bash
# 1. Create a feature branch from develop
git checkout develop
git pull
git checkout -b feature/my-feature

# 2. Make changes and commit frequently
git add .
git commit -m "feat: add enrichment node"

# 3. Push and create a PR
git push -u origin feature/my-feature
# → Create PR on GitHub targeting `develop` branch

# 4. Before requesting review, run the full quality gate
make lint
make typecheck
make test

# 5. After PR approval, squash-merge to develop
# (via GitHub UI)

# 6. For release, create a PR from develop → main
# Tag with appropriate version after merge
```

### Pull Request Checklist

- [ ] `uv lock --check` passes (lockfile is up to date)
- [ ] `uv run ruff check src/ tests/` passes (no lint errors)
- [ ] `uv run mypy src/` passes or errors are explicitly acknowledged
- [ ] `uv run pytest tests/` passes (all tests green)
- [ ] New functionality has test coverage
- [ ] Alembic migration generated (if schema change)
- [ ] Documentation updated (DevGuide, Runbook, or README as applicable)
- [ ] Docker build succeeds locally: `docker build -t test .`
- [ ] **If adding infrastructure dependencies:** Terraform changes are validated (`terraform validate`) and `infra-check` job in `app-python-cicd.yml` is updated

---

## Code Release Process for a Sprint

### 1. Preparation (Sprint End - 2 days)

- [ ] Ensure all feature branches for the sprint are merged to `develop`
- [ ] Create a release branch from `develop`:
  ```bash
  git checkout develop
  git pull
  git checkout -b release/v1.2.0
  ```
- [ ] Update version in `pyproject.toml`
- [ ] Update changelog / release notes

### 2. Release Candidate Testing

```bash
# Run full test suite
make local-up
uv sync
uv run alembic upgrade head
make test-coverage

# Build and test the Docker image locally
docker build -t ai-catalog-agent:rc .
docker run --rm ai-catalog-agent:rc uv run python -c "from src.agents.graph_builder import run_graph; print('OK')"
```

### 3. Release to Production

- [ ] Merge the release branch to `main` via a PR
- [ ] Create a git tag:
  ```bash
  git checkout main
  git pull
  git tag -a v1.2.0-app -m "Release v1.2.0"
  git push origin v1.2.0-app
  ```
- [ ] The CI/CD pipeline (`app-python-cicd.yml`) automatically:
  1. Runs infrastructure validation (ECR, ECS cluster, ECS service check)
  2. Lints, type-checks, and tests
  3. Builds the Docker image
  4. Pushes to ECR
  5. Awaits ECR vulnerability scan completion
  6. Deploys to ECS with a rolling update

- [ ] If infrastructure changes are also part of this release:
  ```bash
  # IMPORTANT: Infrastructure must be applied BEFORE app code pushes
  git tag -a v1.2.0-platform -m "Infra v1.2.0"
  git push origin v1.2.0-platform
  # Wait for infra-02-platform.yml to complete, then push app
  git tag -a v1.2.0-app -m "Release v1.2.0"
  git push origin v1.2.0-app
  ```

### 4. Post-Release

- [ ] Verify ECS service is stable (CloudWatch dashboard)
- [ ] Verify Alembic migrations ran (check `catalog.alembic_version`)
- [ ] Run a manual agent execution to confirm end-to-end
- [ ] Verify the Docker image was pushed to ECR:
  ```bash
  aws ecr list-images --repository-name ai-catalog-agent
  ```
- [ ] Merge `main` back to `develop`:
  ```bash
  git checkout develop
  git pull
  git merge main
  git push
  ```

### 5. Hotfix Flow (for production issues)

```bash
git checkout main
git pull
git checkout -b hotfix/critical-fix
# Fix and commit
git push -u origin hotfix/critical-fix
# → Create PR targeting main directly (skip develop)
# After merge:
git checkout develop
git merge main
git push
```

---

## Testing Strategy

| Layer | Location | Tool | What It Tests |
|-------|----------|------|---------------|
| Unit | `tests/unit/` | pytest | Data models, state transitions, helper functions |
| Integration | `tests/integration/` | pytest + mock_bedrock + mock_s3 | Full graph execution, quality routing, error handling |
| Coverage target | — | pytest-cov | ≥ 80% line coverage |

### Writing Tests

Mock Bedrock for cataloging tests:

```python
@pytest.fixture
def mock_bedrock():
    with patch("boto3.Session") as mock_session:
        mock_client = MagicMock()
        mock_session.return_value.client.return_value = mock_client
        text_response = MagicMock()
        text_response["body"].read.return_value = json.dumps({
            "content": [{"text": "Test description."}]
        }).encode()
        mock_client.invoke_model.side_effect = [text_response]
        yield
```

Run a specific test:

```bash
uv run pytest tests/integration/test_graph.py::TestGraphHappyPath -v
```

---

## Code Quality Standards

- **Formatting:** `ruff format` (line length 100, double quotes)
- **Linting:** `ruff check` (E, F, I, N, W, UP, B, SIM, ARG)
- **Types:** Optional mypy; strict mode off for pragmatism
- **Coverage:** Minimum 80% line coverage (new code should target 90%+)

```bash
# Full quality check before committing
make lint
make typecheck
make test
```

---

## Troubleshooting Local Development

### Docker Compose Issues

```bash
# "port already allocated" on 5433
netstat -ano | findstr :5433  # Windows
lsof -i :5433                 # macOS/Linux
# → Stop other Postgres instances or change DB_PORT in .env

# LocalStack S3 operations fail
curl http://localhost:4566/_localstack/health  # Should show s3:available
docker compose logs -f localstack               # Check for errors

# pgvector extension not found
docker exec ai-catalog-pgvector psql -U postgres -d postgres -c "\dx"
# → Run: docker compose down -v && docker compose up -d (resets DB)
```

### uv / Python Issues

```bash
# uv sync fails
uv lock --check          # Verify lockfile is in sync
uv lock                  # Re-resolve if needed
uv sync                  # Retry

# "No module named 'src'"
# → Ensure PYTHONPATH includes project root
export PYTHONPATH="${PWD}:${PYTHONPATH}"  # macOS/Linux
$env:PYTHONPATH = "${PWD};${env:PYTHONPATH}"  # PowerShell

# uv not found
pip install uv           # Install via pip
# or: curl -LsSf https://astral.sh/uv/install.sh | sh

# Alembic migration fails
uv run alembic history           # Show applied vs pending
uv run alembic upgrade head      # Retry
```

### Agent Execution Issues

```bash
# Graph returns no files
# → Check LocalStack has seeded data
docker exec ai-catalog-localstack awslocal s3 ls s3://ai-catalog-bronze-dev/crm/users/

# Cataloging fails with Bedrock error
# → In local dev, this is expected — falls back to template descriptions
# → Set BEDROCK_DISABLED=true to skip all Bedrock calls

# Profiling takes too long
# → Reduce file size or increase GRAPH_TIMEOUT_SECONDS in .env
```

---

## Pre-commit Hook Setup

```bash
uv run pre-commit install
```