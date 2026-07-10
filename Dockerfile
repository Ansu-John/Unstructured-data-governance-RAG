# =============================================================================
# Dockerfile — Enterprise AI Data Catalog Agent
#
# Multi-stage build:
#   Stage 1 (builder):  Install uv, resolve & sync production dependencies
#   Stage 2 (runtime):  Minimal image with .venv + JDK for PySpark
#
# Build args:
#   BUILD_DATE    – ISO date of the build (for observability labels)
#   BUILD_REVISION – Git SHA of the build
# =============================================================================

# ── Stage 1: Dependency Builder ──────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

ARG BUILD_DATE
ARG BUILD_REVISION

LABEL org.opencontainers.image.title="AI Data Catalog Agent"
LABEL org.opencontainers.image.description="Enterprise AI-Driven Data Quality & Cataloging Agent"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${BUILD_REVISION}"

# Prevent Python from writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install system build dependencies (none needed for pure-Python wheels;
# kept minimal for any sdists that require compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Astral) — pinned to a known-good minor version
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests first (maximize Docker layer caching)
COPY pyproject.toml uv.lock ./

# Sync production dependencies only (exclude dev group)
# --frozen: fail if uv.lock is stale relative to pyproject.toml
# --no-group dev: skip test/lint/dev tooling
RUN uv sync \
    --frozen \
    --no-group dev \
    --no-install-project

# ── Stage 2: Runtime Image ───────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

ARG BUILD_DATE
ARG BUILD_REVISION

LABEL org.opencontainers.image.title="AI Data Catalog Agent"
LABEL org.opencontainers.image.description="Enterprise AI-Driven Data Quality & Cataloging Agent"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${BUILD_REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

# Install runtime system dependencies:
#   - openjdk-17-jre-headless: required by PySpark (Spark JVM runtime)
#   - libpq5: required by psycopg2-binary at runtime
#   - curl: health check utility
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Verify Java installation (PySpark depends on this at import time)
RUN java -version 2>&1 | grep -q "openjdk version" || { echo "Java not found"; exit 1; }

# Create a non-root user for security
RUN groupadd -r catalog && useradd -r -g catalog -d /app -s /sbin/nologin catalog

WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /app/.venv .venv

# Copy application source code
COPY src/ src/
COPY alembic.ini alembic.ini
COPY src/db_migrations/ db_migrations/

# Ensure the non-root user owns the files
RUN chown -R catalog:catalog /app

USER catalog

# Default entry point
ENTRYPOINT ["python", "-m", "src.agents.graph_builder"]

# Override for one-off tasks:
#   docker run --rm <image> uv run python -m src.data_pipeline.quality.gx_suites
#   docker run --rm <image> uv run alembic upgrade head