"""
env.py — Alembic Migration Environment Configuration

Handles database migrations for the catalog schema, LangGraph checkpoint
tables, vector index parameters, and any metadata schema changes.

Alembic is used as the Day 2 schema management tool, decoupling database
changes from the Terraform lifecycle. This is critical because:
  1. Terraform manages infrastructure (cluster, subnet, IAM).
  2. Alembic manages schema (tables, indexes, vector dimensions).
  These change at different rates and are owned by different teams.

Usage:
    alembic init src/db_migrations  (one-time)
    alembic revision --autogenerate -m "add_quality_index"
    alembic upgrade head
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool, create_engine

# Add the project root to the path for model imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

logger = logging.getLogger("alembic.env")

# Alembic Config object
config = context.config

# ---------------------------------------------------------------------------
# Database URL resolution
# ---------------------------------------------------------------------------

def get_database_url() -> str:
    """
    Resolve the database connection string.

    Priority:
      1. sqlalchemy.url in alembic.ini (for local use)
      2. DB_URL environment variable
      3. Constructed from DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD
    """
    url = config.get_main_option("sqlalchemy.url", "")
    if url:
        return url

    url = os.environ.get("DB_URL", "")
    if url:
        return url

    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "aicatalog")
    user = os.environ.get("DB_USER", "catalog_admin")
    password = os.environ.get("DB_PASSWORD", "catalog_dev_pwd_2024")

    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


# ---------------------------------------------------------------------------
# Migration target metadata
# ---------------------------------------------------------------------------

# If you use SQLAlchemy declarative models, import them here so Alembic's
# autogenerate can detect schema changes:
# from src.db_migrations.models import Base
# target_metadata = Base.metadata

target_metadata = None  # No declarative models yet — pure SQL migrations


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine, generating
    SQL scripts that can be run directly against the database later.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    Creates an Engine and associates a connection with the context.
    """
    connectable = create_engine(get_database_url())

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()