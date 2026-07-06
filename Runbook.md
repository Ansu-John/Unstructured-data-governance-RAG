# Operations Runbook — Enterprise AI Data Catalog Agent

**Classification:** Internal — SRE / Platform Team  
**Primary On-Call:** Platform Engineering  
**Escalation:** Data Engineering Lead → CTO  

---

## Day 1: Production Deployment

### Prerequisites

Before the first deployment, an SRE must provision:

1. **S3 State Bucket:** `ai-catalog-terraform-state-<account-id>` (created outside Terraform)
2. **DynamoDB Lock Table:** `ai-catalog-terraform-locks` (created outside Terraform)
3. **GitHub OIDC Provider:** IAM role for GitHub Actions with trust to the repo (`github-actions-terraform-role`)

### Bootstrap Validation

```bash
# Verify all prerequisites before deploying
aws s3api head-bucket --bucket "ai-catalog-terraform-state-$(aws sts get-caller-identity --query Account --output text)"
aws dynamodb describe-table --table-name ai-catalog-terraform-locks --query 'Table.TableStatus'
aws iam get-role --role-name github-actions-terraform-role --query 'Role.Arn'
```

### Deployment Sequence (MANDATORY ORDER)

> **WARNING:** These three tiers MUST be deployed in order. Each tier reads SSM parameters published by the previous tier. Deploying out of order causes unresolvable `data.aws_ssm_parameter` errors.
>
> **IMPORTANT CHANGE:** Tier 2 now also provisions the ECR repository. The `app-python-cicd.yml` pipeline will **fail** at the `infra-check` gate if the ECR repository does not exist. Terraform MUST be applied before any application code is pushed to `main`.

#### Tier 1: Core Static (Network + Storage)

**Change frequency:** 1–2 changes per quarter  
**Blast radius:** Broad (entire network topology)  
**State key:** `dev/core-static/tfstate`

```bash
cd infrastructure/01-core-static/dev

terraform init -backend-config="key=dev/core-static/tfstate"
terraform plan -out=tfplan -var-file=terraform.tfvars
terraform apply tfplan
```

**Resources created:**
- VPC (10.0.0.0/16) with public/private subnets across 2 AZs
- NAT Gateway + Internet Gateway
- S3 VPC Endpoint + DynamoDB VPC Endpoint
- Bronze, Silver, Gold S3 buckets with KMS CMK encryption
- KMS key with automatic rotation

**SSM outputs published:**
| Parameter | Example Value |
|-----------|--------------|
| `/dev/core-static/vpc-id` | `vpc-0a1b2c3d` |
| `/dev/core-static/private-subnet-ids` | `subnet-xxx,subnet-yyy` |
| `/dev/core-static/bronze-bucket-id` | `ai-catalog-dev-bronze` |
| `/dev/core-static/bronze-bucket-arn` | `arn:aws:s3:::ai-catalog-dev-bronze` |
| `/dev/core-static/silver-bucket-id` | `ai-catalog-dev-silver` |
| `/dev/core-static/gold-bucket-id` | `ai-catalog-dev-gold` |
| `/dev/core-static/kms-key-arn` | `arn:aws:kms:us-east-1:...` |

**Verification:**
```bash
aws s3 ls s3://$(aws ssm get-parameter --name /dev/core-static/bronze-bucket-id --query Parameter.Value --output text)
aws ec2 describe-vpcs --vpc-ids $(aws ssm get-parameter --name /dev/core-static/vpc-id --query Parameter.Value --output text)
```

#### Tier 2: Platform Medium (Database + Compute + ECR)

**Change frequency:** 2–4 changes per month  
**Blast radius:** Medium (database resizing, EMR config changes, ECR lifecycle)  
**State key:** `dev/platform-medium/tfstate`

```bash
cd infrastructure/02-platform-medium/dev

terraform init -backend-config="key=dev/platform-medium/tfstate"
terraform plan -out=tfplan
terraform apply tfplan
```

**Resources created:**
- Aurora Serverless v2 PostgreSQL cluster (0.5–4 ACU) with pgvector
- ECS Cluster (Fargate) with Container Insights
- EMR Serverless Application (Spark 3.5)
- **ECR Repository** (`ai-catalog-agent`) with:
  - Image scanning on push (vulnerability detection)
  - Lifecycle policy (14-day untagged image expiry, 1000-image cap)
  - KMS encryption (AES-256 default, KMS CMK if available)
  - Repository policy (least-privilege push/pull for CI/CD and ECS roles)
- **IAM Policies** for CI/CD role (ECR push + ECS deploy permissions)
- CloudWatch Log Groups, Metric Filters, Alarms, Dashboard
- SNS Topic for alarm notifications

**SSM outputs published (NEW for ECR marked ★):**
| Parameter | Purpose |
|-----------|---------|
| `/dev/platform-medium/db-host` | Aurora writer endpoint |
| `/dev/platform-medium/db-name` | Database name (postgres) |
| `/dev/platform-medium/db-secret-arn` | Secrets Manager ARN for credentials |
| `/dev/platform-medium/db-security-group-id` | DB security group |
| `/dev/platform-medium/ecs-cluster-name` | ECS cluster name |
| `/dev/platform-medium/ecs-task-execution-role-arn` | Task execution IAM role |
| `/dev/platform-medium/emr-application-id` | EMR Serverless app ID |
| ★ `/dev/platform-medium/ecr-repository-url` | ECR repository URL (e.g., `<account>.dkr.ecr.us-east-1.amazonaws.com/ai-catalog-agent`) |
| ★ `/dev/platform-medium/ecr-repository-arn` | ECR repository ARN |
| ★ `/dev/platform-medium/ecr-repository-name` | ECR repository name (`ai-catalog-agent`) |

**Verification:**
```bash
# Wait for Aurora to be available (can take 5-10 minutes on first creation)
aws rds describe-db-instances \
    --db-instance-identifier dev-ai-catalog-aurora-writer-1 \
    --query 'DBInstances[0].DBInstanceStatus'

# Test DB connectivity (requires VPN or bastion)
psql -h $(aws ssm get-parameter --name /dev/platform-medium/db-host --query Parameter.Value --output text) \
    -U postgres -d postgres -c "SELECT extname FROM pg_extension;"

# Verify ECR repository was created
aws ecr describe-repositories --repository-names ai-catalog-agent \
    --query 'repositories[0].{name: repositoryName, uri: repositoryUri, scanOnPush: imageScanningConfiguration.scanOnPush}'

# Verify ECR lifecycle policy
aws ecr get-lifecycle-policy --repository-name ai-catalog-agent \
    --query 'lifecyclePolicyText' --output text
```

#### Tier 3: Application Dynamic (Tasks + Events)

**Change frequency:** Every application release  
**Blast radius:** Narrow (task definitions, event rules)  
**State key:** `dev/app-dynamic/tfstate`

```bash
cd infrastructure/03-application-dynamic/dev

terraform init -backend-config="key=dev/app-dynamic/tfstate"
terraform plan -out=tfplan
terraform apply tfplan
```

**Resources created:**
- ECS Task Definitions (agent orchestrator, dbt runner)
- ECS Fargate Service with security group
- EventBridge Rules (S3 landing trigger + periodic quality check)
- Application-level CloudWatch Alarms

**Post-Deployment:**
```bash
# 1. Run Alembic migrations against the new Aurora cluster
uv run alembic upgrade head

# 2. Verify the agent can connect and execute
uv run python -c "from src.agents.graph_builder import run_graph; result = run_graph(); print('OK')"

# 3. Verify ECS service is healthy
aws ecs describe-services \
    --cluster dev-ai-catalog-ecs \
    --services dev-ai-catalog-agent-svc \
    --query 'services[0].{status: status, running: runningCount, desired: desiredCount}'
```

### CI/CD Tag-Based Promotion

| Action | Tag | Pipeline |
|--------|-----|----------|
| Network/Storage change | `v1.2.3-core` | `infra-01-static.yml` |
| Platform change (incl. ECR) | `v1.2.3-platform` | `infra-02-platform.yml` |
| App change | `v1.2.3-app` | `infra-03-app-dynamic.yml` |
| Push to `main` | (automatic) | `app-python-cicd.yml` |

> **NEW:** Before the app pipeline runs `docker-build`, it now executes an `infra-check` job that validates the ECR repository, ECS cluster, and ECS service all exist. If infrastructure is missing, the pipeline fails immediately with a clear error message pointing to the exact Terraform command to run — no more waiting 10 minutes for tests to pass only to fail at push time.

---

## Day 2: Operations Runbooks

### RB-000: ECR Repository Issues

**Severity:** High  
**Symptoms:** CI/CD pipeline fails at `infra-check` job with "ECR repository not found" or at docker push step.

**Root Causes:**
- Tier 2 Terraform not applied before app push
- Accidental deletion of the ECR repository
- Lifecycle policy expired all images in use
- ECR repository policy drift

**Diagnosis:**
```bash
# Step 1: Check if the repository exists
aws ecr describe-repositories --repository-names ai-catalog-agent \
    --query 'repositories[0].repositoryUri'

# Step 2: Check lifecycle policy
aws ecr get-lifecycle-policy --repository-name ai-catalog-agent

# Step 3: Check repository policy (permissions)
aws ecr get-repository-policy --repository-name ai-catalog-agent \
    --query 'policyText' --output text

# Step 4: List images in the repository
aws ecr list-images --repository-name ai-catalog-agent \
    --query 'imageIds[?imageTag==`null`]' --max-items 20
```

**Resolution:**
```bash
# Option A: Apply Terraform to recreate/repair the repository
cd infrastructure/02-platform-medium/dev
terraform init
terraform plan -out=tfplan
terraform apply tfplan

# Option B: If the repository was deleted, check Terraform state
terraform state list | grep ecr_repository
# If the resource is still in state but deleted externally:
terraform apply   # Terraform will recreate it

# Option C: Manually force image lifecycle policy evaluation
aws ecr start-lifecycle-policy-preview --repository-name ai-catalog-agent
aws ecr start-image-scan --repository-name ai-catalog-agent \
    --image-id imageTag=latest
```

**Prevention:**
- The `infra-check` job in `app-python-cicd.yml` catches missing ECR repos before build
- IAM policies restrict ECR deletion to the Terraform role only
- Enable Terraform state versioning for recovery (S3 bucket versioning)

---

### RB-001: Database Connection Loss

**Severity:** Critical  
**Symptoms:** Agent errors contain `psycopg2.OperationalError`, CloudWatch alarm `agent-loop-stall` firing, ECS task health checks failing.

**Root Causes:**
- Aurora Serverless scaling event (cold start)
- Maintenance window failover
- Security group drift
- VPC Endpoint failure

**Diagnosis:**
```bash
# Step 1: Check Aurora status
aws rds describe-db-instances \
    --db-instance-identifier dev-ai-catalog-aurora-writer-1 \
    --query 'DBInstances[0].{Status:DBInstanceStatus,Endpoint:Endpoint.Address,Scaling:ServerlessV2ScalingConfiguration}'

# Step 2: Check recent RDS events
aws rds describe-events \
    --source-type db-instance \
    --source-identifier dev-ai-catalog-aurora-writer-1 \
    --duration 360

# Step 3: Query CloudWatch for error rate
aws logs insights-query \
    --log-group-names /ecs/ai-catalog-agent/dev \
    --query-string "fields @timestamp, @message | filter @message like /OperationalError|ConnectionError/ | sort @timestamp desc | limit 20" \
    --start-time -3600
```

**Resolution:**
```bash
# Option A: Force ECS service restart (re-establishes connection pool)
aws ecs update-service \
    --cluster dev-ai-catalog-ecs \
    --service dev-ai-catalog-agent-svc \
    --force-new-deployment

# Option B: If Aurora is scaling, increase min ACU to prevent recurrence
# Edit infrastructure/02-platform-medium/dev/main.tf:
#   serverless_min_capacity = 1.0
cd infrastructure/02-platform-medium/dev
terraform apply

# Option C: If Security Group is the issue, verify ingress
aws ec2 describe-security-group-rules \
    --filter Name=group-id,Values=<sg-xxx> \
    --query 'SecurityGroupRules[?FromPort==`5432`]'
```

**Prevention:**
- Set `serverless_min_capacity = 1.0` in production
- The `compile_graph_with_checkpointer()` function has automatic fallback to `MemorySaver` — but this loses state across restarts
- Monitor `DBConnections` metric in the CloudWatch dashboard

---

### RB-002: Alembic Migration Failure

**Severity:** High  
**Symptoms:** `uv run alembic upgrade head` fails with errors, deployment pipeline stuck at migration step.

**Diagnosis:**
```bash
# Check current migration state
uv run alembic current                     # What's applied?
uv run alembic history                     # Full migration chain

# Check for conflicting migrations
uv run alembic check                       # Detect unapplied or conflicting revisions
```

**Common Failures & Resolutions:**

**Failure A — "relation already exists":**
```bash
# The migration was partially applied. Mark it as applied without running:
uv run alembic stamp <revision-id>
```

**Failure B — "column referenced in index does not exist" (pgvector):**
```bash
# The vector extension may not be loaded. Verify:
psql -h <aurora-endpoint> -U postgres -d postgres -c "SELECT * FROM pg_extension WHERE extname='vector';"

# If missing, load it (requires rds_superuser):
psql -h <aurora-endpoint> -U postgres -d postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**Failure C — Authentication failure:**
```bash
# Check the DB credentials in Secrets Manager
aws secretsmanager get-secret-value \
    --secret-id $(aws ssm get-parameter --name /dev/platform-medium/db-secret-arn --query Parameter.Value --output text) \
    --query SecretString --output text | jq .

# Verify alembic.ini sqlalchemy.url is correct
# For Aurora, use: postgresql+psycopg://catalog_admin:<password>@<host>:5432/postgres
```

**Rollback Procedure:**
```bash
# Check migration history
uv run alembic history

# Roll back one step
uv run alembic downgrade -1

# Roll back to a specific revision
uv run alembic downgrade 0001

# Re-apply after fixing the issue
uv run alembic upgrade head
```

> **Rule:** Never edit an applied migration in production. Create a new revision to fix it.

---

### RB-003: Stuck Agent Execution Loop

**Severity:** High  
**Symptoms:** `agent-loop-stall` alarm firing; repeated "Retry N/3" log messages; no catalog entries created in hours.

**Root Causes:**
- All Bronze data fails quality checks (upstream data quality regression)
- Bedrock throttling (ThrottlingException)
- pgvector index corruption

**Diagnosis:**
```bash
# Step 1: Check recent agent execution logs
aws logs tail /ecs/ai-catalog-agent/dev --since 30m

# Step 2: Query quality_runs for recent failures
# (via ECS task exec or database client)
aws ecs execute-command \
    --cluster dev-ai-catalog-ecs \
    --task <task-id> \
    --container agent-orchestrator \
    --command "/bin/bash" \
    --interactive

# Inside container:
psql \$DB_URL -c "
  SELECT run_id, asset_name, success, score, failed_expectations
  FROM catalog.quality_runs
  WHERE run_timestamp > NOW() - INTERVAL '1 hour'
  ORDER BY run_timestamp DESC;"

# Step 3: Check for stuck checkpoints
psql \$DB_URL -c "
  SELECT thread_id, checkpoint_id, created_at
  FROM langgraph.checkpoints
  WHERE created_at > NOW() - INTERVAL '1 hour'
  ORDER BY created_at DESC;"
```

**Resolution:**

```bash
# Option A: Clear the stuck thread (loses state but unblocks)
psql \$DB_URL -c "
  DELETE FROM langgraph.checkpoints WHERE thread_id = 'stuck-thread-id';"

# Option B: Temporarily lower quality threshold (if upstream issue is acknowledged)
# Set ECS environment override and force new deployment
aws ecs update-service \
    --cluster dev-ai-catalog-ecs \
    --service dev-ai-catalog-agent-svc \
    --force-new-deployment \
    --environment-overrides name=QUALITY_THRESHOLD,value=0.80

# Option C: Force-restart the ECS service
aws ecs update-service \
    --cluster dev-ai-catalog-ecs \
    --service dev-ai-catalog-agent-svc \
    --force-new-deployment

# Option D: Bypass Bedrock (use template descriptions if Bedrock is degraded)
aws ecs update-service \
    --cluster dev-ai-catalog-ecs \
    --service dev-ai-catalog-agent-svc \
    --force-new-deployment \
    --environment-overrides name=BEDROCK_DISABLED,value=true
```

**Prevention:**
- Configure `agent-loop-stall` alarm with PagerDuty integration
- Subscribe the SNS topic to an operational email list

---

### RB-004: Reading OpenTelemetry Traces in CloudWatch

**Severity:** Medium  
**Symptoms:** Need to trace a failed LangGraph node execution across service boundaries.

**Diagnosis:**
```bash
# Step 1: Find the trace ID from the agent log
aws logs insights-query \
    --log-group-names /ecs/ai-catalog-agent/dev \
    --query-string "fields @timestamp, @message, @traceId | filter @message like /ERROR|FAIL|exception/ | sort @timestamp desc | limit 20"

# Step 2: Query all spans for a specific trace ID
aws logs insights-query \
    --log-group-names /ecs/ai-catalog-agent/dev \
    --query-string "fields @timestamp, @spanId, @parentSpanId, @message | filter @traceId = '<trace-id>' | sort @timestamp"

# Step 3: Cross-reference with quality pipeline logs
aws logs insights-query \
    --log-group-names /quality/gx-runs/dev \
    --query-string "fields @timestamp, @message | filter @message like /FAIL|quarantine/ | sort @timestamp desc | limit 50"
```

**CloudWatch Dashboard:**
- Navigate to CloudWatch → Dashboards → `dev-ai-catalog-dashboard`
- Widgets show: Agent Performance (success/error counts), Quality Metrics (failures/quarantines), Recent Errors

**Filter patterns for common signals:**
| Signal | Log Group | Pattern |
|--------|-----------|---------|
| Quality failure | `/langgraph/ai-catalog-agent/dev` | `"QUALITY FAIL"` |
| Quarantine event | `/quality/gx-runs/dev` | `"quarantine"` |
| Agent crash | `/ecs/ai-catalog-agent/dev` | `"CRITICAL"` or `"Traceback"` |
| Bedrock throttling | `/ecs/ai-catalog-agent/dev` | `"ThrottlingException"` or `"retry"` |

---

### RB-005: Recovering a Falsely Quarantined Dataset

**Severity:** Medium  
**Symptoms:** A dataset was quarantined by Great Expectations but subsequent analysis showed the data was valid (false positive GX rule).

**Diagnosis:**
```bash
# Step 1: Find the quarantine run
aws s3 ls s3://ai-catalog-bronze-dev/_quarantine/

# Step 2: Inspect the quarantined data (Parquet)
aws s3 cp s3://ai-catalog-bronze-dev/_quarantine/<asset_name>/run_id=<run_id>/ ./quarantine/ --recursive
# Use PySpark or Parquet CLI to inspect
parquet-tools inspect ./quarantine/

# Step 3: Check what specific expectations failed
# Query quality_runs table for the run_id
psql \$DB_URL -c "SELECT validation_json->'results' FROM catalog.quality_runs WHERE run_id = '<run_id>';"
```

**Resolution:**
```bash
# Option A: Manually promote to Silver (if you've verified the data is clean)
aws s3 cp \
    s3://ai-catalog-bronze-dev/_quarantine/<asset_name>/run_id=<run_id>/ \
    s3://ai-catalog-silver-dev/<asset_name>/ \
    --recursive

# Option B: Relax the false-positive expectation and re-run
# Edit the expectation suite in gx_suites.py, then run:
uv run python -m src.data_pipeline.quality.gx_suites \
    --bronze-path s3://ai-catalog-bronze-dev/<source>/<object>/ \
    --silver-path s3://ai-catalog-silver-dev/<source>/<object>/ \
    --suite-name <suite_name> \
    --expectation-threshold 0.95

# Option C: Re-run with lower threshold if the suite is generally too strict
uv run python -m src.data_pipeline.quality.gx_suites \
    --bronze-path s3://ai-catalog-bronze-dev/<source>/<object>/ \
    --silver-path s3://ai-catalog-silver-dev/<source>/<object>/ \
    --suite-name <suite_name> \
    --expectation-threshold 0.80
```

**Post-Recovery:**
```bash
# Update the expectation threshold in the expectation suite to prevent recurrence
# The validation_json column contains the full run details
# Adjust the specific expectation in SUITE_REGISTRY and commit
```

---

### RB-006: Bedrock Throttling / Rate Limiting

**Severity:** Medium  
**Symptoms:** `ThrottlingException` in logs, cataloging completes slowly, `tenacity` retry logs show backoff.

**Built-in Resilience:**
The cataloging node wraps Bedrock API calls with `tenacity`:

```python
bedrock_retry = retry(
    retry=retry_if_exception_type(BEDROCK_RETRYABLE),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=1, max=60, jitter=2),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
```

**Tuning (if throttling persists):**
```python
# In src/agents/nodes/cataloging.py, adjust parameters:
bedrock_retry = retry(
    retry=retry_if_exception_type(BEDROCK_RETRYABLE),
    stop=stop_after_attempt(6),                  # Increase from 4
    wait=wait_exponential_jitter(initial=2,       # Longer initial wait
                                 max=120,          # Higher cap
                                 jitter=5),        # Larger jitter
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
```

**Emergency bypass:**
```bash
# Set environment variable to force all Bedrock calls to fallback mode
export BEDROCK_DISABLED=true
uv run python -m src.agents.graph_builder

# Or via ECS:
aws ecs update-service \
    --cluster dev-ai-catalog-ecs \
    --service dev-ai-catalog-agent-svc \
    --force-new-deployment \
    --environment-overrides name=BEDROCK_DISABLED,value=true
```

---

### RB-007: ECS Task Crash / OOM

**Severity:** Critical  
**Symptoms:** ECS service shows `Task stopped` repeatedly, `MemoryUtilization` > 90%, task exit code 137 (OOM-killed).

**Diagnosis:**
```bash
# Step 1: Check stopped task reason
aws ecs describe-tasks \
    --cluster dev-ai-catalog-ecs \
    --tasks $(aws ecs list-tasks --cluster dev-ai-catalog-ecs --desired-status STOPPED --query 'taskArns[0]' --output text) \
    --query 'tasks[0].{exitCode: containers[0].exitCode, reason: stoppedReason}'

# Step 2: Check resource utilization
aws cloudwatch get-metric-statistics \
    --namespace AWS/ECS \
    --metric-name MemoryUtilization \
    --dimensions Name=ServiceName,Value=dev-ai-catalog-agent-svc \
    --start-time -3600 --end-time 0 --period 60 \
    --statistics Maximum

# Step 3: Check for large files causing OOM
aws s3 ls --summarize --human-readable --recursive s3://ai-catalog-bronze-dev/crm/users/
```

**Resolution:**
```yaml
# Increase task resources in 03-application-dynamic/dev/ecs_tasks.tf:
resource "aws_ecs_task_definition" "agent_orchestrator" {
  cpu    = "2048"    # Was 1024
  memory = "6144"    # Was 3072
}
```

```bash
# Re-apply and force new deployment
cd infrastructure/03-application-dynamic/dev
terraform apply
aws ecs update-service \
    --cluster dev-ai-catalog-ecs \
    --service dev-ai-catalog-agent-svc \
    --force-new-deployment
```

---

### RB-008: Recovering from Terraform State Corruption

**Severity:** Critical  
**Symptoms:** `terraform plan` shows unexpected resource destruction, state lock errors, or `ResourceNotFound` errors.

**Procedure:**
```bash
# Step 1: Identify which tier is affected
cd infrastructure/<affected-tier>/dev

# Step 2: Force unlock if state is stuck
terraform force-unlock <lock-id>

# Step 3: If state is corrupted, restore from the S3 versioning
aws s3api list-object-versions \
    --bucket ai-catalog-terraform-state-<account-id> \
    --prefix dev/<tier>/tfstate

# Step 4: Restore the previous working version
aws s3api get-object \
    --bucket ai-catalog-terraform-state-<account-id> \
    --key dev/<tier>/tfstate \
    --version-id <previous-version> \
    restored.tfstate

# Step 5: Upload restored state (with extreme caution)
aws s3 cp restored.tfstate s3://ai-catalog-terraform-state-<account-id>/dev/<tier>/tfstate

# Step 6: Re-run plan to verify
terraform plan
```

---

### RB-009: uv Lockfile Conflict After Rebase

**Severity:** Low  
**Symptoms:** `uv sync --frozen` fails in CI with `uv.lock` mismatch; developer sees merge conflicts in `uv.lock`.

**Resolution:**
```bash
# Step 1: After rebasing on develop, if uv.lock has conflicts:
uv lock --no-update  # Re-generate uv.lock from pyproject.toml without upgrading
# or simply:
uv lock              # Full re-resolve (fast — typically 1-3 seconds)

# Step 2: Verify the lockfile is consistent
uv lock --check

# Step 3: Commit the resolved uv.lock
git add uv.lock
git commit -m "chore: resolve uv.lock after rebase"
```

**Prevention:**
- `uv.lock` is cross-platform and deterministic; most conflicts resolve cleanly with `uv lock --no-update`
- Unlike `poetry.lock` (which was slow to regenerate), `uv lock` completes in seconds even for large dependency trees

---

### RB-010: Docker Build Failure (uv Sync)

**Severity:** High  
**Symptoms:** CI pipeline fails at Docker build stage with `uv sync --frozen` error.

**Diagnosis:**
```bash
# Step 1: Check if the lockfile is out of sync
uv lock --check

# Step 2: Test the Docker build locally
docker build --no-cache -t ai-catalog-agent:test -f Dockerfile .

# Step 3: If the python:3.12-slim image lacks system dependencies, check apt
docker run --rm python:3.12-slim-bookworm bash -c "java -version 2>&1 || echo 'JDK missing'"
```

**Resolution:**
```bash
# Option A: If uv.lock needs updating
uv lock
git add uv.lock
git commit -m "fix: update uv.lock for Docker build"

# Option B: If a dependency added a C extension requirement not in Dockerfile
# Add the missing system package to Dockerfile (both builder and runtime stages)
# e.g., RUN apt-get install -y libssl-dev

# Option C: Clear Docker build cache and retry
docker build --no-cache -t ai-catalog-agent:test -f Dockerfile .
```

---

### RB-011: ECR Vulnerability Scan — Critical Finding

**Severity:** High  
**Symptoms:** ECR image scan reports CRITICAL or HIGH vulnerabilities; security team requires remediation before deployment.

**Diagnosis:**
```bash
# Step 1: Get scan results for the latest image
aws ecr describe-image-scan-findings \
    --repository-name ai-catalog-agent \
    --image-id imageTag=latest \
    --query 'imageScanFindings.{severityCounts: findingSeverityCounts, findings: findings[?severity==`CRITICAL` || severity==`HIGH`]}' \
    --output json

# Step 2: Check if the vulnerabilities are in base image or application deps
# The scan output includes 'name' (CVE), 'uri', and 'attributes' with package info
```

**Resolution:**
```bash
# Option A: Rebuild with updated base image (patch the base image tag in Dockerfile)
# Update FROM python:3.12-slim-bookworm to latest security patch
docker build --no-cache -t ai-catalog-agent:fixed -f Dockerfile .
docker tag ai-catalog-agent:fixed <ecr-url>/ai-catalog-agent:fixed
docker push <ecr-url>/ai-catalog-agent:fixed

# Option B: If vulnerabilities are in application dependencies, update and rebuild
uv lock --upgrade-package <affected-package>
git add uv.lock pyproject.toml
git commit -m "fix: upgrade <package> to resolve CVE-xxxx"

# Option C: Accept the risk (if vulnerabilities are in the base image and no patch exists)
# File a security exception and track the CVE
```

**Prevention:**
- ECR `scan_on_push = true` ensures every image is scanned as it's pushed
- Use minimal base images (`python:3.12-slim-bookworm` instead of `python:3.12`)
- The CI/CD pipeline includes an `infra-check` step that waits for scan completion and reports findings

---

### RB-012: ECR Lifecycle Policy — Accidental Image Expiry

**Severity:** Medium  
**Symptoms:** A specific image tag used in a running ECS task was deleted by the lifecycle policy; rollback to a specific version fails.

**Diagnosis:**
```bash
# Step 1: Check what images remain in the repository
aws ecr list-images --repository-name ai-catalog-agent \
    --query 'imageIds[*].imageTag' --output json

# Step 2: Check lifecycle policy execution history
aws ecr get-lifecycle-policy-preview --repository-name ai-catalog-agent
```

**Resolution:**
```bash
# Option A: Rebuild the missing tag from the Git SHA
git checkout <git-sha>
docker build -t ai-catalog-agent:restored .
docker tag ai-catalog-agent:restored <ecr-url>/ai-catalog-agent:<git-sha>
docker push <ecr-url>/ai-catalog-agent:<git-sha>

# Option B: If you need to increase image retention, update the lifecycle policy
# Edit ecr.tf and increase max_image_count or untagged_image_expire_days
cd infrastructure/02-platform-medium/dev
terraform apply

# Option C: Tag a previously untagged image to prevent deletion
# Find the image digest first
aws ecr list-images --repository-name ai-catalog-agent \
    --query 'imageIds[?imageTag==`null`]' --output json
# Then tag it
aws ecr batch-get-image --repository-name ai-catalog-agent \
    --image-ids imageDigest=<sha256:xxx> --output json \
    | jq '.images[0].imageManifest' -r > /tmp/manifest.json
aws ecr put-image --repository-name ai-catalog-agent \
    --image-tag rescued --image-manifest file:///tmp/manifest.json
```

**Prevention:**
- The lifecycle policy has a 14-day grace period before expiring untagged images
- Tagged images (like `latest`, `v*`) are never expired — only the total image count cap applies
- Always tag important images with the Git SHA (the CI/CD pipeline does this automatically)

---

## Operational Metrics

| Metric | Source | Alert Threshold | Alert Name | Action |
|--------|--------|----------------|------------|--------|
| `CatalogSuccessCount` | Agent logs | Sum < 1 in 15min | `agent-loop-stall` | RB-003 |
| `QualityFailureCount` | Quality logs | Sum > 5 in 10min | `quality-drop` | RB-005 |
| `AgentErrorCount` | Agent logs | Sum > 10 in 10min | `agent-error-rate` | RB-004 |
| `MemoryUtilization` | ECS metrics | > 85% avg | `ecs-oom-risk` | RB-007 |
| `DBConnections` | RDS metrics | > 80% of max | `db-connection-pool` | RB-001 |
| `CPUUtilization` | ECS metrics | > 90% avg | `ecs-cpu-risk` | RB-007 |

---

## On-Call Checklist

### Initial Triage (First 5 Minutes)

1. **Is the agent loop stalled?** → Check `agent-loop-stall` alarm → RB-003
2. **Are quality failures spiking?** → Check `quality-drop` alarm → RB-005
3. **Is the database reachable?** → Check `db-connection-pool` → RB-001
4. **Are ECS tasks crashing?** → Check `ecs-oom-risk` → RB-007
5. **Is the CI/CD pipeline failing?** → Check `infra-check` logs → RB-000

### Escalation Path

| If | Then |
|----|------|
| Single agent node failing | Check logs, restart service |
| Database unreachable for > 5min | Page DBA / escalate to Platform Lead |
| Terraform state corrupted | Page Platform Lead (RB-008) |
| Data loss suspected | Page Data Engineering Lead + CTO |

---

## Appendices

### A — Useful CloudWatch Logs Insights Queries

```sql
# All errors in the last hour
fields @timestamp, @message
| filter @message like /ERROR|CRITICAL|Traceback/
| sort @timestamp desc
| limit 50

# Agent performance over time
stats count() by bin(5m), @message like /Cataloged/
| filter @message like /Cataloged/

# Quality pass/fail ratio
stats count() as total, count_if(@message like /QUALITY PASS/) as passed, count_if(@message like /QUALITY FAIL/) as failed

# Bedrock throttling events
fields @timestamp, @message
| filter @message like /ThrottlingException|retry_attempt|tenacity/
| sort @timestamp desc
```

### B — SSM Parameter Names (Dev Environment)

| Path | Source Tier | Example |
|------|-------------|---------|
| `/dev/core-static/vpc-id` | 01-core-static | `vpc-xxx` |
| `/dev/core-static/private-subnet-ids` | 01-core-static | `subnet-xxx,subnet-yyy` |
| `/dev/core-static/bronze-bucket-id` | 01-core-static | `ai-catalog-dev-bronze` |
| `/dev/core-static/silver-bucket-id` | 01-core-static | `ai-catalog-dev-silver` |
| `/dev/core-static/gold-bucket-id` | 01-core-static | `ai-catalog-dev-gold` |
| `/dev/core-static/kms-key-arn` | 01-core-static | `arn:aws:kms:...` |
| `/dev/platform-medium/db-host` | 02-platform-medium | `xxx.cluster-yyy.us-east-1.rds.amazonaws.com` |
| `/dev/platform-medium/db-secret-arn` | 02-platform-medium | `arn:aws:secretsmanager:...` |
| `/dev/platform-medium/ecs-cluster-name` | 02-platform-medium | `dev-ai-catalog-ecs` |
| `/dev/platform-medium/emr-application-id` | 02-platform-medium | `app-xxx` |
| ★ `/dev/platform-medium/ecr-repository-url` | 02-platform-medium | `<account>.dkr.ecr.us-east-1.amazonaws.com/ai-catalog-agent` |
| ★ `/dev/platform-medium/ecr-repository-arn` | 02-platform-medium | `arn:aws:ecr:us-east-1:<account>:repository/ai-catalog-agent` |
| ★ `/dev/platform-medium/ecr-repository-name` | 02-platform-medium | `ai-catalog-agent` |

### C — Required IAM Permissions for ECS Task Role

```json
{
    "Effect": "Allow",
    "Action": [
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject"
    ],
    "Resource": [
        "arn:aws:s3:::ai-catalog-*-dev",
        "arn:aws:s3:::ai-catalog-*-dev/*"
    ]
},
{
    "Effect": "Allow",
    "Action": "bedrock:InvokeModel",
    "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
    ]
},
{
    "Effect": "Allow",
    "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
    ],
    "Resource": "arn:aws:logs:*:*:log-group:/ecs/ai-catalog-agent/*"
},
{
    "Effect": "Allow",
    "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
    ],
    "Resource": "*"
}
```

### D — IAM Permissions for GitHub Actions CI/CD Role

These permissions are required on the `github-actions-terraform-role` bootstrap role. Terraform (in `02-platform-medium/dev/iam.tf`) attaches the ECR push and ECS deploy policies automatically, but the base role must exist first.

**Minimum bootstrap role trust policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:<org>/<repo>:*"
        }
      }
    }
  ]
}
```

**Permissions Terraform attaches (auto-provisioned):**

| Policy Name | Actions | Purpose |
|-------------|---------|---------|
| `{env}-ecr-push-policy` | `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer`, `ecr:DescribeRepositories`, `ecr:ListImages` | Push Docker images to ECR |
| `{env}-ecs-deploy-policy` | `ecs:DescribeTaskDefinition`, `ecs:RegisterTaskDefinition`, `ecs:DescribeServices`, `ecs:UpdateService`, `ecs:DescribeClusters`, `ecs:ListTasks`, `ecs:DescribeTasks`, `ecs:WaitUntilServicesStable`, `iam:PassRole` | Deploy to ECS |

### E — Poetry → uv Command Migration Reference

| Action | Old (Poetry) | New (uv) |
|--------|-------------|----------|
| Install dependencies | `poetry install` | `uv sync` |
| Install production only | `poetry install --only main` | `uv sync --no-group dev` |
| Add a dependency | `poetry add requests` | `uv add requests` |
| Add a dev dependency | `poetry add --group dev pytest` | `uv add --group dev pytest` |
| Remove a dependency | `poetry remove requests` | `uv remove requests` |
| Update lockfile | `poetry lock` | `uv lock` |
| Check lockfile | (no direct equivalent) | `uv lock --check` |
| Run a command in venv | `poetry run pytest` | `uv run pytest` |
| Activate venv shell | `poetry shell` | `source .venv/bin/activate` |
| Build a package | `poetry build` | `uv build` |
| Publish to PyPI | `poetry publish` | `uv publish` |
| Show dependency tree | `poetry show --tree` | `uv tree`