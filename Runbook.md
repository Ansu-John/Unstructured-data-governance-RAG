
## Repository Layout

```
ai-data-catalog-agent/
│
├── .github/workflows/          # CI/CD: 3 infra tiers + 1 app pipeline
│   ├── infra-01-static.yml     #   Network + Storage (tag: v*-core)
│   ├── infra-02-platform.yml   #   DB + Compute (tag: v*-platform)
│   ├── infra-03-app-dynamic.yml#   Tasks + Events (tag: v*-app)
│   └── app-python-cicd.yml     #   Docker → ECR → ECS rolling deploy
│
├── infrastructure/
│   ├── modules/                # Reusable Terraform modules
│   │   ├── networking/         # VPC, subnets, NAT, VPC endpoints
│   │   ├── storage_lake/       # Bronze/Silver/Gold S3 + KMS
│   │   ├── compute_base/       # ECS cluster + EMR Serverless
│   │   ├── database_aurora/    # Aurora Serverless v2 + pgvector
│   │   └── observability/      # CloudWatch logs, dashboards, alarms
│   │
│   ├── 01-core-static/dev/     # LIFECYCLE TIER 1 (lowest change freq)
│   │   ├── backend.tf          #   State: /dev/core-static/tfstate
│   │   ├── main.tf             #   VPC + S3 + KMS
│   │   └── outputs.tf          #   → SSM Parameter Store
│   │
│   ├── 02-platform-medium/dev/ # LIFECYCLE TIER 2 (medium change freq)
│   │   ├── backend.tf          #   State: /dev/platform-medium/tfstate
│   │   ├── data.tf             #   ← SSM from tier 1
│   │   ├── main.tf             #   Aurora + ECS + EMR + Observability
│   │   └── outputs.tf          #   → SSM Parameter Store
│   │
│   └── 03-application-dynamic/dev/  # LIFECYCLE TIER 3 (highest change freq)
│       ├── backend.tf          #   State: /dev/app-dynamic/tfstate
│       ├── data.tf             #   ← SSM from tiers 1 & 2
│       ├── variables.tf        #   Input variables
│       ├── ecs_tasks.tf        #   Task definitions + services
│       ├── eventbridge.tf      #   S3 landing triggers + schedules
│       └── dynamic_alerts.tf   #   App-level CloudWatch alarms
│
├── src/
│   ├── data_pipeline/
│   │   ├── quality/
│   │   │   └── gx_suites.py    # GX + PySpark validation engine
│   │   └── transformations/
│   │       ├── dbt_project.yml # dbt project manifest
│   │       └── models/         # Silver → Gold SQL models
│   ├── db_migrations/          # Alembic migrations
│   │   ├── env.py              # Alembic environment config
│   │   └── versions/           # Versioned migration scripts
│   ├── agents/
│   │   ├── state.py            # AgentState TypedDict + domain models
│   │   ├── graph_builder.py    # StateGraph assembly + conditional router
│   │   └── nodes/
│   │       ├── ingestion.py    # Bronze S3 scanner
│   │       ├── profiling.py    # Schema profiling (PySpark)
│   │       └── cataloging.py   # LLM description + pgvector write
│   └── common/
│       ├── config.py           # Pydantic settings (env-based config)
│       └── telemetry.py        # OpenTelemetry bootstrap
│
├── local_development/
│   ├── docker-compose.yml      # Postgres + pgvector + LocalStack
│   └── init-scripts/
│       ├── 01-init.sql         # Schema bootstrap (DDL + indexes)
│       └── 02-init-s3-buckets.sh # S3 bucket creation + seeding
│
├── tests/
│   ├── integration/
│   │   └── test_graph.py       # Full graph integration tests
│   └── unit/
│       └── test_nodes.py       # Standalone node unit tests
│
├── pyproject.toml              # Poetry manifest
├── .gitignore
└── README.md                   # This file
```

---

## Day 1: Local Development Deployment

### Prerequisites

- Docker Desktop 4.25+
- Python 3.12+
- Poetry 1.8+
- AWS CLI (for LocalStack compatibility)
- Make (optional, but helpful)

### Step 1: Start the Local Stack

```bash
cd local_development

# Start PostgreSQL + pgvector + LocalStack (S3 mock)
docker compose up -d

# Verify health
docker compose ps
# Both postgres and localstack should show "healthy"

# Verify PostgreSQL + pgvector
docker exec ai-catalog-pgvector psql -U postgres -d postgres -c "SELECT extname, extversion FROM pg_extension;"
# Should show: vector, uuid-ossp, pg_stat_statements

# Verify S3 buckets were created
docker exec ai-catalog-localstack awslocal s3 ls
# Should show: ai-catalog-bronze-dev, ai-catalog-silver-dev, ai-catalog-gold-dev
```

### Step 2: Install Python Dependencies

```bash
cd ..

# Install with Poetry (creates .venv)
poetry install

# Activate the virtual environment
poetry shell
```

### Step 3: Run Alembic Migrations

```bash
# Run all pending migrations
alembic upgrade head

# Verify migration status
alembic current

# Verify tables were created
docker exec ai-catalog-pgvector psql -U postgres -d postgres -c "\dt catalog.*"
docker exec ai-catalog-pgvector psql -U postgres -d postgres -c "\dt langgraph.*"
```

### Step 4: Run the Agent Graph Locally

```bash
# Run the full LangGraph pipeline
python -m src.agents.graph_builder

# Expected output:
#   Files discovered:    2
#   Profiles computed:   1
#   Catalog entries:     1
#   Errors:              0
```

### Step 5: Run the Data Quality Pipeline

```bash
# Execute Great Expectations validation on Bronze data
python -m src.data_pipeline.quality.gx_suites \
    --bronze-path s3://ai-catalog-bronze-dev/crm/users/ \
    --silver-path s3://ai-catalog-silver-dev/crm/users/ \
    --quarantine-path s3://ai-catalog-bronze-dev/_quarantine/ \
    --suite-name crm_users_suite \
    --expectation-threshold 0.95
```

### Step 6: Run Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=term-missing

# Run specific test categories
pytest tests/unit/ -v
pytest tests/integration/ -v
```

### Local Development Quick Reference

```bash
# Stop and clean everything
docker compose down -v        # Removes volumes (data loss!)
docker compose down           # Keeps volumes

# Reset the database
docker compose down -v
docker compose up -d

# Tail logs
docker compose logs -f postgres
docker compose logs -f localstack

# Access PostgreSQL directly
docker exec -it ai-catalog-pgvector psql -U postgres -d postgres
```

---

## Day 1: Production Deployment (Terraform Lifecycle)

### Bootstrap Prerequisites

Before the first deployment, establish:

1. **S3 State Bucket:** `ai-catalog-terraform-state` (created outside Terraform)
2. **DynamoDB Lock Table:** `ai-catalog-terraform-locks` (created outside Terraform)
3. **GitHub OIDC Provider:** IAM role for GitHub Actions (`github-actions-terraform`)

### Deployment Order (MANDATORY)

These three tiers MUST be deployed in sequence. Each tier reads SSM parameters published by the previous tier.

#### Tier 1: Core Static (Network + Storage)

```bash
cd infrastructure/01-core-static/dev

terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

**What it creates:**
- VPC with public/private subnets across 2 AZs
- NAT Gateway + Internet Gateway
- S3 VPC Endpoint + DynamoDB VPC Endpoint
- Bronze, Silver, Gold S3 buckets with KMS encryption
- **Publishes output to SSM:** `/dev/core-static/vpc-id`, `/dev/core-static/private-subnet-ids`, `/dev/core-static/bronze-bucket-id`, etc.

**Blast radius:** Broad (entire network topology). Changes here affect all downstream tiers. Expect 1–2 changes per quarter.

#### Tier 2: Platform Medium (Database + Compute)

```bash
cd infrastructure/02-platform-medium/dev

terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

**What it creates:**
- Aurora Serverless v2 PostgreSQL cluster (0.5–4 ACU)
- ECS Cluster (Fargate) with Container Insights
- EMR Serverless Application (Spark)
- CloudWatch Log Groups, Dashboards, SNS Alarm Topic
- **Publishes to SSM:** `/dev/platform-medium/db-host`, `/dev/platform-medium/ecs-cluster-name`, `/dev/platform-medium/db-secret-arn`

**Blast radius:** Medium. DB resizing, parameter group changes, EMR config updates. Expect 2–4 changes per month.

#### Tier 3: Application Dynamic (Tasks + Events)

```bash
cd infrastructure/03-application-dynamic/dev

terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

**What it creates:**
- ECS Task Definitions (agent orchestrator, dbt runner)
- ECS Service with security group
- EventBridge Rules (S3 landing trigger + periodic quality check)
- Application-level CloudWatch Alarms (stall detection, quality spikes)

**Blast radius:** Narrow. Task def revisions, event rule changes, alarm tuning. Expect changes on every application release.

### CI/CD Tag-Based Promotion

| Tag Pattern | Tier | What Deploys |
|-------------|------|-------------|
| `v1.2.3-core` | 01-core-static | VPC + S3 + KMS |
| `v1.2.3-platform` | 02-platform-medium | DB + ECS + EMR |
| `v1.2.3-app` | 03-application-dynamic | Tasks + Events + Alarms |
| Push to `main` | Application | Docker build → ECR → ECS rolling deploy |

### Post-Deployment Steps

```bash
# 1. Run Alembic migrations against the new Aurora cluster
alembic upgrade head

# 2. Verify the agent can connect
python -c "from src.agents.graph_builder import run_graph; result = run_graph(); print('Graph execution successful')"
```

---

## Day 2: Operations Runbooks

### Runbook 1: Transient Database Connection Loss

**Symptoms:** Agent errors contain `psycopg2.OperationalError: connection to server`, CloudWatch alarm `db-connection-failure` firing.

**Root Causes:**
- Aurora Serverless scaling event (0.5 ACU → 4 ACU)
- Maintenance window failover
- Network ACL / Security Group misconfiguration
- VPC Endpoint transient failure

**Resolution:**

```bash
# Step 1: Verify DB is reachable from within the VPC
aws rds describe-db-instances \
    --db-instance-identifier dev-ai-catalog-aurora-writer-1 \
    --query 'DBInstances[0].{Status:DBInstanceStatus,Endpoint:Endpoint.Address}'

# Step 2: Check Aurora scaling events
aws rds describe-events \
    --source-type db-instance \
    --source-identifier dev-ai-catalog-aurora-writer-1 \
    --duration 360

# Step 3: Verify Security Group ingress
aws ec2 describe-security-group-rules \
    --filter Name=group-id,Values=<sg-xxx> \
    --query 'SecurityGroupRules[?FromPort==`5432`]'

# Step 4: If DB is healthy, restart the ECS service
aws ecs update-service \
    --cluster dev-ai-catalog-ecs \
    --service dev-ai-catalog-agent-svc \
    --force-new-deployment

# Step 5 (if persistent): Increase min ACU to prevent scale-to-zero
# Edit infrastructure/02-platform-medium/dev/main.tf:
#   serverless_min_capacity = 1.0
# Then apply
cd infrastructure/02-platform-medium/dev
terraform apply
```

**Prevention:**
- Set `serverless_min_capacity = 1.0` for production (prevents cold starts)
- Configure RDS Proxy for connection pooling (not shown in this version)
- Implement circuit breaker in the agent (built-in: retry_router retries up to 3 times)

### Runbook 2: Incremental Schema Migration via Alembic

**Scenario:** Adding a new column `data_classification` to the `catalog.data_assets` table.

```bash
# Step 1: Generate a new migration revision
alembic revision --autogenerate -m "add_data_classification"

# Step 2: Review and edit the generated file
# File: src/db_migrations/versions/0003_add_data_classification.py
```

```python
# migration content:
def upgrade():
    op.add_column(
        "data_assets",
        sa.Column("data_classification", sa.Text(),
                  server_default="internal"),
        schema="catalog",
    )

def downgrade():
    op.drop_column("data_assets", "data_classification", schema="catalog")
```

```bash
# Step 3: Apply the migration
alembic upgrade head

# Step 4: Verify
docker exec -it ai-catalog-pgvector psql -U postgres -d postgres \
    -c "\d catalog.data_assets"

# Step 5: Commit the migration file to version control
git add src/db_migrations/versions/0003_add_data_classification.py
git commit -m "feat: add data_classification column to data_assets"
```

**Rollback procedure:**

```bash
# Check migration history
alembic history

# Roll back one step
alembic downgrade -1

# Roll back to a specific revision
alembic downgrade 0001
```

**Important:** Never edit an existing migration that has been applied to production. Always create a new revision.

### Runbook 3: Recovering Stuck Agent Execution Loops

**Symptoms:** `agent-loop-stall` alarm firing; CloudWatch log shows repeated "Retry N/3 — routing back to ingestion" messages.

**Root Causes:**
- All Bronze data fails quality checks (upstream data quality regression)
- Bedrock model invocation timeout (throttling)
- pgvector index corruption

**Diagnosis:**

```bash
# Step 1: Check the last N agent execution logs
aws logs tail /langgraph/ai-catalog-agent/dev --since 30m

# Step 2: Check quality run history directly on the DB
docker exec -it ai-catalog-pgvector psql -U postgres -d postgres \
    -c "SELECT run_id, asset_name, success, score, failed_expectations, execution_secs
        FROM catalog.quality_runs
        WHERE run_timestamp > NOW() - INTERVAL '1 hour'
        ORDER BY run_timestamp DESC;"

# Step 3: Check for stuck checkpoints
docker exec -it ai-catalog-pgvector psql -U postgres -d postgres \
    -c "SELECT thread_id, checkpoint_id, created_at
        FROM langgraph.checkpoints
        WHERE created_at > NOW() - INTERVAL '1 hour'
        ORDER BY created_at DESC;"
```

**Resolution:**

```bash
# Option A: Reset the stuck thread (loses context but unblocks)
# (Set via environment or DB)
docker exec -it ai-catalog-pgvector psql -U postgres -d postgres \
    -c "DELETE FROM langgraph.checkpoints WHERE thread_id = 'stuck-thread-id';"

# Option B: Increase quality threshold temporarily (if upstream issue is known)
export QUALITY_THRESHOLD=0.80
python -m src.agents.graph_builder

# Option C: Bypass Bedrock (use template descriptions)
export BEDROCK_DISABLED=true
python -m src.agents.graph_builder

# Option D: Force-restart the ECS service
aws ecs update-service \
    --cluster dev-ai-catalog-ecs \
    --service dev-ai-catalog-agent-svc \
    --force-new-deployment
```

**Prevention:**
- Configure the `agent_stall` alarm with a PagerDuty integration
- Set up a CloudWatch Logs Insights query dashboard to monitor retry rates
- Implement dead-letter handling for persistently failing files (planned feature)

### Runbook 4: Bedrock Rate Limit Management

**Symptoms:** `ThrottlingException` in agent logs; cataloging node fails; `tenacity` retry logs show backoff.

**Built-in Resilience:**

The cataloging node wraps all Bedrock API calls with `tenacity` retry decorators:
- `stop_after_attempt=4` — retries up to 4 times
- `wait_exponential_jitter(initial=1, max=60, jitter=2)` — exponential backoff with jitter
- Logs each retry attempt at WARNING level

**Tuning:**

```python
# In src/agents/nodes/cataloging.py, adjust:
bedrock_retry = retry(
    retry=retry_if_exception_type(BEDROCK_RETRYABLE),
    stop=stop_after_attempt(6),        # Increase from 4
    wait=wait_exponential_jitter(initial=2, max=120, jitter=5),  # Longer backoff
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
```

### Runbook 5: S3 Data Skew / Large File Handling

**Symptoms:** Profiling node timeout; ECS task OOM; PySpark driver crash.

**Diagnosis:**

```bash
# Check file sizes in Bronze
aws s3 ls --summarize --human-readable --recursive s3://ai-catalog-bronze-dev/crm/users/

# Check ECS task resource utilization (CloudWatch metrics)
# CPUUtilization, MemoryUtilization > 85%
```

**Resolution:**

```yaml
# Increase ECS task resources in 03-application-dynamic/dev/ecs_tasks.tf:
resource "aws_ecs_task_definition" "agent_orchestrator" {
  cpu    = "2048"    # Was 1024
  memory = "6144"    # Was 3072
}
```

---