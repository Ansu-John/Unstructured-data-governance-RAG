

## Development Guide

### Adding a New Expectation Suite

```python
# In src/data_pipeline/quality/gx_suites.py, add to SUITE_REGISTRY:

SUITE_REGISTRY = {
    # ... existing suites ...
    "new_suite": [
        ("expect_column_values_to_not_be_null", {"column": "order_id"}, {"priority": "critical"}),
        ("expect_column_values_to_be_between", {"column": "amount", "min_value": 0}, {"priority": "high"}),
    ],
}
```

### Adding a New Graph Node

1. Create the node function in `src/agents/nodes/`:

```python
# new_node.py
def new_node(state: AgentState) -> Dict[str, Any]:
    with tracer.start_as_current_span("new_node") as span:
        try:
            # ... implementation ...
            return {"key": "value"}
        except Exception as exc:
            span.record_exception(exc)
            return {"error": str(exc), "errors": [...]}
```

2. Register in `graph_builder.py`:

```python
workflow.add_node("new_node", new_node)
workflow.add_edge("profiling", "new_node")
workflow.add_edge("new_node", "cataloging")
```

### Creating a Terraform Module

```bash
# 1. Create the module directory
mkdir -p infrastructure/modules/example/

# 2. Create main.tf with input/output variables
touch infrastructure/modules/example/main.tf

# 3. Consume in the appropriate tier
# In infrastructure/02-platform-medium/dev/main.tf:
module "example" {
  source = "../../modules/example"
  environment = local.environment
  tags = local.tags
}
```

---

## CI/CD Pipelines

### Infrastructure Pipelines (Manual Tag-Based)

| Pipeline | Trigger | Approvals |
|----------|---------|-----------|
| `infra-01-static.yml` | Tag: `v*-core` or workflow_dispatch | Manual apply gate |
| `infra-02-platform.yml` | Tag: `v*-platform` or workflow_dispatch | Manual apply gate |
| `infra-03-app-dynamic.yml` | Tag: `v*-app` or workflow_dispatch | Manual apply gate |

### Application Pipeline (Push-Triggered)

`app-python-cicd.yml` runs on every push to `main` or `develop`:

```
Lint (ruff) → Type-check (mypy) → Test (pytest) → Docker Build → ECR Push → ECS Deploy
```

The ECS deploy uses:
- **Rolling update** with a circuit breaker (rolls back on deployment failure)
- **Task definition rendering** via `amazon-ecs-render-task-definition`
- **Service stability wait** before marking as successful

---

## Security & Compliance

### Data Protection

- **At rest:** All S3 buckets encrypted with KMS CMK (AES-256)
- **In transit:** TLS enforced via S3 bucket policy (Deny `aws:SecureTransport=false`)
- **Database:** Aurora storage encryption enabled; credentials stored in AWS Secrets Manager
- **Vector embeddings:** Stored in encrypted PostgreSQL; no PII in embedding plaintext

### Network Security

- All resources deployed in private subnets (no public IPs except NAT Gateway)
- S3 access via VPC Endpoint (no internet traversal)
- Database security group allows only ECS/EMR security groups on port 5432

### IAM Least Privilege

- ECS task execution role limited to:
  - Specific S3 buckets (`ai-catalog-*-dev`)
  - Specific Bedrock model ARNs
  - Specific CloudWatch log groups
- No wildcard resource grants on data plane operations
- GitHub Actions uses OIDC (no long-lived AWS keys)

### Audit Trail

- CloudTrail enabled on all S3 buckets (management + data events)
- LangGraph checkpoint table provides full execution history
- `catalog.quality_runs` table is append-only (immutable audit log)
- CloudWatch Log Groups retain according to environment (7d dev, 30d staging, 90d prod)

---

## Operational Metrics

| Metric | Source | Alert Threshold | Purpose |
|--------|--------|----------------|---------|
| `CatalogSuccessCount` | Agent logs | < 1 in 60 min | Detect stalled agent loop |
| `QualityFailureCount` | Agent logs | > 10 in 15 min | Upstream data quality regression |
| `QuarantineEventCount` | Quality logs | Anomaly detection | Abnormal quarantine patterns |
| `AgentErrorCount` | Agent logs | > 10 in 15 min | Application errors / Bedrock throttling |
| `MemoryUtilization` | ECS metrics | < 20% avg | Under-provisioned or stuck container |

---

## Architectural Decision Records

### ADR-001: Ephemeral GX Context over File-Based Context

**Decision:** Use `EphemeralDataContext` (in-memory) instead of a file-based `DataContext`.

**Rationale:** File-based contexts require a Great Expectations deployment directory, YAML configuration, and shared file storage. Ephemeral contexts are created at runtime, need no persistent storage, and can be embedded in PySpark jobs without external dependencies. The tradeoff is that validation history is not stored in GX's built-in store — we persist it ourselves in `catalog.quality_runs`.

### ADR-002: SSM Parameter Store over Terraform Data Sources

**Decision:** Cross-tier state references use SSM Parameter Store, not `terraform_remote_state` data sources.

**Rationale:** `terraform_remote_state` creates tight coupling — if the upstream state changes format or is moved, all downstream tiers break. SSM parameters are a stable API contract: one tier publishes, another reads. The publishing tier can be refactored as long as the SSM parameter names remain stable.

### ADR-003: pgvector over Dedicated Vector Database

**Decision:** Use PostgreSQL + pgvector instead of Pinecone, Weaviate, or OpenSearch.

**Rationale:** For this system's scale (< 10M vectors, < 1536 dimensions), pgvector with IVFFlat indexing provides adequate recall (~0.95) and query latency (< 50ms) while eliminating the operational overhead of managing a separate vector database. The Aurora PostgreSQL cluster already serves as the LangGraph checkpointer, so there is no additional infrastructure to operate. If scale exceeds pgvector's capabilities, the cataloging node's `_persist_to_vector_store` function can be swapped to a dedicated vector DB client without changing the agent graph.