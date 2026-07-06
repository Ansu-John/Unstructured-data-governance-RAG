-- ============================================================================
-- 01-init.sql
-- Enterprise AI-Driven Data Quality & Cataloging Agent
-- Local Development Database Bootstrap
--
-- Activates pgvector, creates the metadata catalog schema, and establishes
-- the LangGraph checkpoint persistence structures required by the agent
-- orchestration runtime.
-- ============================================================================

-- Enable required extensions ------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Create the catalog admin role (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'catalog_admin') THEN
        CREATE ROLE catalog_admin WITH LOGIN PASSWORD 'catalog_admin_dev';
    END IF;
END
$$;

-- Catalog metadata schema ----------------------------------------------------
CREATE SCHEMA IF NOT EXISTS catalog AUTHORIZATION catalog_admin;
CREATE SCHEMA IF NOT EXISTS langgraph AUTHORIZATION catalog_admin;

-- ============================================================================
-- LANGGRAPH CHECKPOINT PERSISTENCE
-- These tables mirror the interface expected by
-- langgraph.checkpoint.postgres.PostgresSaver.
-- ============================================================================

CREATE TABLE IF NOT EXISTS langgraph.checkpoints (
    thread_id        TEXT        NOT NULL,
    checkpoint_ns    TEXT        NOT NULL DEFAULT '',
    checkpoint_id    TEXT        NOT NULL,
    parent_checkpoint_id TEXT,
    type             TEXT,
    checkpoint       JSONB       NOT NULL,
    metadata         JSONB       DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS langgraph.checkpoint_writes (
    thread_id        TEXT        NOT NULL,
    checkpoint_ns    TEXT        NOT NULL DEFAULT '',
    checkpoint_id    TEXT        NOT NULL,
    task_id          TEXT        NOT NULL,
    idx              INTEGER     NOT NULL,
    channel          TEXT        NOT NULL,
    type             TEXT,
    value            JSONB       NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_created_at
    ON langgraph.checkpoints (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_checkpoint_writes_lookup
    ON langgraph.checkpoint_writes (thread_id, checkpoint_ns);

-- ============================================================================
-- DATA CATALOG — VECTOR STORE
-- Stores metadata embeddings for semantic search over data assets.
-- ============================================================================

CREATE TABLE IF NOT EXISTS catalog.data_assets (
    asset_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_name      TEXT        NOT NULL,
    asset_type      TEXT        NOT NULL CHECK (asset_type IN ('table','view','file','stream','topic','model')),
    source_system   TEXT        NOT NULL,
    schema_name     TEXT,
    table_name      TEXT,
    file_path       TEXT,
    description     TEXT,
    tags            TEXT[]      DEFAULT '{}',
    quality_score   REAL        DEFAULT 0.0,
    row_count       BIGINT      DEFAULT 0,
    partition_count INTEGER     DEFAULT 0,
    embedding       VECTOR(1536),          -- Amazon Titan Embedding dimension
    metadata_json   JSONB       DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Explicitly named table-level constraints
    CONSTRAINT data_assets_asset_type_check CHECK (asset_type IN ('table','view','file','stream','topic','model')),
    CONSTRAINT uq_data_assets_file_path UNIQUE (file_path)
);

CREATE INDEX IF NOT EXISTS idx_data_assets_embedding
    ON catalog.data_assets
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_data_assets_source_system
    ON catalog.data_assets (source_system);

CREATE INDEX IF NOT EXISTS idx_data_assets_tags
    ON catalog.data_assets USING GIN (tags);


-- ============================================================================
-- QUALITY METRICS HISTORY
-- Immutable log of every Great Expectations validation run.
-- ============================================================================


CREATE TABLE IF NOT EXISTS catalog.quality_runs (
    run_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_name      TEXT        NOT NULL,
    source_path     TEXT        NOT NULL,
    run_timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    success         BOOLEAN     NOT NULL,
    score           REAL        NOT NULL DEFAULT 0.0,
    threshold       REAL        NOT NULL DEFAULT 0.95,
    total_expectations INTEGER  NOT NULL DEFAULT 0,
    failed_expectations  INTEGER NOT NULL DEFAULT 0,
    validation_json JSONB       NOT NULL DEFAULT '{}'::jsonb,
    quarantine_path TEXT,
    execution_secs  REAL        NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_quality_runs_asset
    ON catalog.quality_runs (asset_name, run_timestamp DESC);

-- ============================================================================
-- AGENT EXECUTION LOG
-- Tracks every LangGraph agent cycle for observability and debugging.
-- ============================================================================

CREATE TABLE IF NOT EXISTS catalog.agent_executions (
    execution_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    thread_id       TEXT        NOT NULL,
    graph_name      TEXT        NOT NULL DEFAULT 'data_quality_catalog',
    node_name       TEXT        NOT NULL,
    status          TEXT        NOT NULL CHECK (status IN ('running','completed','failed','quarantined')),
    input_summary   JSONB       DEFAULT '{}'::jsonb,
    output_summary  JSONB       DEFAULT '{}'::jsonb,
    error_message   TEXT,
    duration_ms     INTEGER     DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_executions_thread
    ON catalog.agent_executions (thread_id, created_at DESC);

-- ============================================================================
-- GRANT DEFAULT PRIVILEGES
-- ============================================================================

ALTER SCHEMA catalog OWNER TO catalog_admin;
ALTER SCHEMA langgraph OWNER TO catalog_admin;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA catalog TO catalog_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA catalog TO catalog_admin;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA langgraph TO catalog_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA langgraph TO catalog_admin;

CREATE TABLE IF NOT EXISTS catalog.quality_runs (
    run_id VARCHAR(255) PRIMARY KEY,
    asset_name VARCHAR(255),
    success BOOLEAN,
    score NUMERIC,
    threshold NUMERIC,
    quarantine_path TEXT,
    execution_secs NUMERIC,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);