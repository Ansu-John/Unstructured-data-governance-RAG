"""
0002_tune_vector_index.py — Alembic Migration

Tunes the pgvector IVFFlat index parameters based on production
observations. This is a typical Day 2 migration: the index lists
parameter was conservatively set to 100 in the initial migration; after
profiling the vector distribution, we increase it to 200 for better
recall at the cost of a small index build-time increase.

This migration demonstrates the operational pattern for schema changes
that are decoupled from infrastructure code — the database cluster
(managed by Terraform 02-platform-medium) is unchanged; only the schema
(managed by Alembic) evolves.

Revision ID: 0002
Revises: 0001
Create Date: 2024-08-15
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """
    Rebuild the vector index with tuned parameters.

    The IVFFlat index is dropped and recreated atomically. The index
    rebuild is a write lock on the data_assets table but should complete
    in under a second for tables with < 1M rows.
    """
    # Drop existing index
    op.execute(
        "DROP INDEX IF EXISTS catalog.idx_data_assets_embedding"
    )

    # Recreate with tuned lists parameter
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_assets_embedding "
        "ON catalog.data_assets "
        "USING ivfflat (embedding::vector(1536) vector_cosine_ops) "
        "WITH (lists = 200)"  # Increased from 100 for better recall
    )

    # Also add a dedicated index on quality_score for common filtering queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_assets_quality_score "
        "ON catalog.data_assets (quality_score DESC) "
        "WHERE quality_score > 0.0"
    )


def downgrade() -> None:
    """Revert to the original index configuration."""
    op.execute("DROP INDEX IF EXISTS catalog.idx_data_assets_quality_score")
    op.execute("DROP INDEX IF EXISTS catalog.idx_data_assets_embedding")

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_assets_embedding "
        "ON catalog.data_assets "
        "USING ivfflat (embedding::vector(1536) vector_cosine_ops) "
        "WITH (lists = 100)"
    )