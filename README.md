# Enterprise AI-Driven Data Quality & Cataloging Agent

**Version:** 1.0.0  
**Architecture:** Medallion (Bronze → Silver → Gold) + LangGraph State Machine  
**Stack:** PySpark, Great Expectations, LangGraph, Aurora PostgreSQL + pgvector, Amazon Bedrock  
**Deployment:** Terraform (3-tier segregated state) + ECS Fargate + EMR Serverless

---


## System Architecture

```mermaid
flowchart LR
  classDef awsService fill:#ff9900,stroke:#232f3e,stroke-width:2px,color:#000;
  classDef storage fill:#3f8624,stroke:#232f3e,stroke-width:2px,color:#fff;
  classDef model fill:#00a4a6,stroke:#232f3e,stroke-width:2px,color:#fff;

  %% Data Ingestion
  subgraph Ingestion [Data Source Ingestion]
    direction TB
    Docs[PDF / Data Files] --> S3_Bronze[(Amazon S3\nBronze Bucket)]:::storage
  end

  %% EMR Pipeline
  subgraph EMR [AWS EMR Serverless]
    direction TB
    Parse[PDF Parsing & Extraction] --> DQ[Data Quality\nGreat Expectations]
    DQ --> DBT[SQL Transformations\ndbt Core]
  end

  %% Agent Orchestration (ECS)
  subgraph ECS [AWS ECS Fargate - FastAPI]
    direction TB
    subgraph LangGraph [LangGraph Agents]
        direction TB
        Ingest[Ingestion Agent] --> Profiler[Profiling & DQ Agent]
        Profiler --> Catalog[Cataloging Agent]
        Catalog --> Review[Human Review Node]
        Review --> State[State Management]
        State -. Condition atov .-> Ingest
    end
  end

  %% AI Models
  subgraph Bedrock [Amazon Bedrock]
    direction TB
    Claude[Anthropic Claude\nMetadata Gen/Remediation]:::model
    Titan[Amazon Titan\nVector Generation]:::model
  end

  %% Storage & State
  subgraph Persistent [Enterprise Storage & Catalog]
    direction TB
    S3_Gold[(Amazon S3\nSilver/Gold)]:::storage
    Aurora[(Aurora PostgreSQL\nState Persistence)]:::storage
    PgVector[(pgvector\nANN Search)]:::storage
  end

  %% Consumers
  subgraph Consumers [Consumers]
    direction TB
    UI[Engineer UI\nHuman-in-the-Loop]
    Glossary[Business Glossaries &\nData Discovery]
  end

  %% Relationships & Routing
  S3_Bronze -- S3 Event Trigger --> Parse
  S3_Bronze <--> |Intermediate Chunks| EMR
  
  Parse -- Text Chunks/Embeddings --> Ingest
  
  DBT <--> |Read/Write| S3_Gold
  DQ -- Quality Results --> S3_Gold
  
  LangGraph <--> |Control LLM| Claude
  LangGraph <--> |Embeddings Generation| Titan
  
  State <--> |LangGraph Checkpointing| Aurora
  Catalog <--> |Vector Rules & Ops| PgVector
  
  LangGraph <--> |Human Review| UI
  LangGraph --> Glossary
```

### Core Stack Decisions

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Storage** | S3 (Bronze/Silver/Gold) | Immutable object store with Hive-style partitioning; cost-effective for data lake patterns |
| **Batch Processing** | PySpark on EMR Serverless | Distributed compute for large-volume Bronze → Silver validation; serverless to avoid idle cluster cost |
| **Data Quality** | Great Expectations | Declarative expectation suites; native PySpark integration; in-memory ephemeral context avoids deployment overhead |
| **Orchestration** | LangGraph on ECS Fargate | Stateful graph with Postgres checkpointing; conditional routing for quality gates; retry loops |
| **Vector Store** | Aurora PostgreSQL + pgvector | Single managed service for both LangGraph state persistence and vector ANN search; no external vector DB to operate |
| **LLM Integration** | Amazon Bedrock (Claude 3.5 Sonnet + Titan Embeddings) | No GPU infrastructure to manage; native AWS IAM integration; lowest-latency option within VPC |
| **State Segregation** | Terraform (3 states) | Isolate blast radius: networking changes don't affect DB, DB changes don't affect task definitions |
| **Schema Migrations** | Alembic | Decouple schema evolution from Terraform; application-owned schema changes without infrastructure review |
| **Observability** | OpenTelemetry → CloudWatch | Vendor-neutral instrumentation; structured JSON logging for metric extraction via CloudWatch Logs Insights |

---
