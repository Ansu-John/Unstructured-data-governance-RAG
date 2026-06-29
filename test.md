# 🤖 AI-Driven Data Quality & Cataloging Agent (Enterprise Edition)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![PySpark](https://img.shields.io/badge/PySpark-Distributed-orange)
![Snowflake](https://img.shields.io/badge/Snowflake-Cortex-lightblue)
![AWS](https://img.shields.io/badge/AWS-Event--Driven-yellow)
![LangGraph](https://img.shields.io/badge/LangGraph-Agentic-green)

An enterprise-grade, highly scalable Retrieval-Augmented Generation (RAG) pipeline and autonomous AI Agent. This system is designed to ingest massive volumes of complex unstructured data (financial reports, nested contracts, scanned PDFs), extract structured metadata via distributed compute, and serve grounded, context-aware insights through a secure, stateless LLM API.

## 🚀 Overview

Transitioning from a functional AI prototype to a production system requires solving for compute bottlenecks, data governance, and state management. This architecture decouples ingestion from extraction using event-driven AWS services, leverages PySpark Pandas UDFs for memory-safe PDF parsing, and utilizes Snowflake Cortex to ensure that sensitive embeddings and LLM inferences never leave the data warehouse boundary. 

## Key Enterprise Features

- **Event-Driven & Decoupled Ingestion**: S3 Event Notifications and SQS queues provide a durable buffer, preventing upstream upload spikes from overwhelming the compute layer.
- **Memory-Safe Distributed Extraction**: Utilizes PySpark Pandas UDFs operating on S3 URIs (rather than raw binaries). This prevents driver Out-Of-Memory (OOM) errors and drastically reduces SerDe overhead when processing massive PDFs.
- **Automated Data Quality & DLQ**: Corrupted or unparseable files are gracefully caught and routed to a Dead Letter Queue (DLQ) bucket for inspection, while dbt tests enforce strict schema and data constraints before vectorization.
- **In-Database AI (Zero Egress)**: Snowflake Cortex natively generates vector embeddings (e5-base-v2) and executes LLM completions (llama3-70b) directly on the compute nodes where the data resides.
- **Stateless, Checkpointed Agent**: The LangGraph agent runs in a scalable FastAPI Docker container on AWS ECS. It utilizes a PostgreSQL checkpointer to persist conversational state, ensuring durability against container failures.
- **Secure Credential Management**: Zero hardcoded secrets. The application authenticates via IAM task roles and fetches database credentials and API keys dynamically from AWS SSM Parameter Store.

## 🛠️ Technology Stack

- Cloud & Orchestration: AWS (S3, SQS, EMR, ECS Fargate), Apache Airflow (MWAA)
- Distributed Processing: Apache Spark (PySpark), pdfplumber, Pandas, Apache Arrow
- Data Warehouse & AI: Snowflake, Snowpipe, Snowflake Cortex, dbt (Data Build Tool)
- Agentic Framework: LangChain, LangGraph, FastAPI, PostgreSQL (State Management)
- Infrastructure & Security: Terraform (IaC), AWS IAM, AWS SSM Parameter Store
- CI/CD & Testing: GitHub Actions, Pytest, Docker, Poetry

## 🏗️ System Architecture

The pipeline is segregated into four scalable tiers: **Event-Driven Ingestion**, **Distributed Extraction**, **Unified Data & AI Storage**, and **Stateless Agentic Serving**.

```mermaid
flowchart TD
    %% Ingestion Layer
    User[User / App] -->|Upload PDFs| S3_Bronze[S3: bronze-pdfs]
    S3_Bronze -->|Event Notification| SQS[AWS SQS Queue]
    SQS -->|Reads URIs| Airflow[Apache Airflow / MWAA]
    
    %% Extraction Layer
    Airflow -->|Orchestrates Job| EMR[AWS EMR: PySpark]
    EMR -->|Pandas UDF Parsing| S3_Bronze
    EMR -->|Success| S3_Silver[S3: silver-text JSON]
    EMR -->|Failure DLQ| S3_DLQ[S3: DLQ-pdfs]
    
    %% Storage & AI Layer
    S3_Silver -->|Snowpipe| Snow_Raw[Snowflake: Bronze]
    Snow_Raw -->|dbt Tests| Snow_Clean[Snowflake: Silver / Mart]
    Snow_Clean -->|Cortex Embeddings| Snow_Vector[(Snowflake: Vector & Metadata)]
    
    %% Serving Layer
    Client_Query[User Query] <--> API[FastAPI + LangGraph on AWS ECS]
    API -->|Fetch Secure Configs| SSM[AWS SSM Parameter Store]
    API <-->|State Checkpointing| DB[(PostgreSQL State)]
    API <-->|RAG & Cortex LLM| Snow_Vector

    %% Styling
    style S3_Bronze fill:#FF9900,color:black
    style S3_Silver fill:#FF9900,color:black
    style S3_DLQ fill:#FF9900,color:black
    style SQS fill:#FF4F8B,color:white
    style Airflow fill:#017CEE,color:white
    style EMR fill:#FF9900,color:black
    style Snow_Raw fill:#29B5E8,color:black
    style Snow_Clean fill:#29B5E8,color:black
    style Snow_Vector fill:#29B5E8,color:black
    style API fill:#00A67E,color:white
    style SSM fill:#3F8624,color:white
    style DB fill:#336791,color:white
