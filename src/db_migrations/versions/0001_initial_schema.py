"""
0001_initial_schema.py — Alembic Migration

Creates the initial catalog schema and LangGraph persistence tables.

This migration mirrors the 01-init.sql local-dev script so that production
environments get identical structures via the migration pipeline.

Revision ID: 0001
Revises: None (first migration)
Create Date: 2024-07-01
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Apply the initial schema."""
    # Enable extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # LangGraph checkpoints table
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("metadata_", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
        schema="langgraph",
    )

    # LangGraph checkpoint writes table
    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
        schema="langgraph",
    )

    # Catalog schema
    op.execute("CREATE SCHEMA IF NOT EXISTS catalog")

    # Data assets table (vector store)
    op.create_table(
        "data_assets",
        sa.Column("asset_id", sa.UUID(), nullable=False, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("asset_name", sa.Text(), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("schema_name", sa.Text(), nullable=True),
        sa.Column("table_name", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.ARRAY(sa.Text()), nullable=True, server_default="{}"),
        sa.Column("quality_score", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("row_count", sa.BigInteger(), nullable=True, server_default="0"),
        sa.Column("partition_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("embedding", sa.Text(), nullable=True),  # will be cast to vector
        sa.Column("metadata_json", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("asset_id"),
        schema="catalog",
    )

    # Add check constraint for asset_type
    op.execute(
        "ALTER TABLE catalog.data_assets "
        "ADD CONSTRAINT data_assets_asset_type_check "
        "CHECK (asset_type IN ('table','view','file','stream','topic','model'))"
    )

    # Add vector index (ivfflat for approximate nearest neighbor)
    # Note: The embedding column needs to be cast to vector type first.
    # This is done in a separate step because Alembic doesn't natively
    # support pgvector.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_assets_embedding "
        "ON catalog.data_assets "
        "USING ivfflat (embedding::vector(1536) vector_cosine_ops) "
        "WITH (lists = 100)"
    )

    # Quality runs table
    op.create_table(
        "quality_runs",
        sa.Column("run_id", sa.UUID(), nullable=False, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("asset_name", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("run_timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("threshold", sa.Float(), nullable=False, server_default="0.95"),
        sa.Column("total_expectations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_expectations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("quarantine_path", sa.Text(), nullable=True),
        sa.Column("execution_secs", sa.Float(), nullable=False, server_default="0.0"),
        sa.PrimaryKeyConstraint("run_id"),
        schema="catalog",
    )

    # Agent executions log
    op.create_table(
        "agent_executions",
        sa.Column("execution_id", sa.UUID(), nullable=False, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("graph_name", sa.Text(), nullable=False, server_default="data_quality_catalog"),
        sa.Column("node_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=True),
        sa.Column("output_summary", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("execution_id"),
        schema="catalog",
    )

    # Performance indexes
    op.create_index(
        "idx_checkpoints_created_at",
        "checkpoints",
        ["created_at"],
        postgresql_using="btree",
        schema="langgraph",
    )
    op.create_index(
        "idx_data_assets_source_system",
        "data_assets",
        ["source_system"],
        schema="catalog",
    )
    op.create_index(
        "idx_data_assets_tags",
        "data_assets",
        ["tags"],
        postgresql_using="gin",
        schema="catalog",
    )
    op.create_index(
        "idx_quality_runs_asset",
        "quality_runs",
        ["asset_name", sa.text("run_timestamp DESC")],
        schema="catalog",
    )


def downgrade() -> None:
    """Roll back the initial schema."""
    op.drop_table("data_assets", schema="catalog")
    op.drop_table("quality_runs", schema="catalog")
    op.drop_table("agent_executions", schema="catalog")
    op.drop_table("checkpoint_writes", schema="langgraph")
    op.drop_table("checkpoints", schema="langgraph")
    op.execute("DROP SCHEMA IF EXISTS catalog CASCADE")
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")