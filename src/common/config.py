"""
config.py — Environment Configuration Loading Engine

Centralizes all environment variable parsing for the entire application.
Every module should import configuration from here rather than calling
os.environ directly, ensuring a single source of truth for all tunables.

Uses pydantic's BaseSettings for validated, typed, documented configuration
with .env file support for local development.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeploymentEnvironment(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class Settings(BaseSettings):
    """
    Application-wide settings, loaded from environment variables and .env file.

    All values have sensible defaults for local development (Docker Compose).
    Override via environment variables in production (ECS / EMR Serverless).
    """

    # ── General ─────────────────────────────────────────────────────────
    environment: DeploymentEnvironment = DeploymentEnvironment.LOCAL
    service_name: str = "ai-data-catalog-agent"
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    # ── Database (PostgreSQL + pgvector) ────────────────────────────────
    db_host: str = Field(default="localhost", env="DB_HOST")
    db_port: int = Field(default=5433, env="DB_PORT")
    db_name: str = Field(default="postgres", env="DB_NAME")
    db_user: str = Field(default="postgres", env="DB_USER")
    db_password: str = Field(default="", env="DB_PASSWORD")
    db_min_connections: int = Field(default=2, env="DB_MIN_CONNECTIONS")
    db_max_connections: int = Field(default=10, env="DB_MAX_CONNECTIONS")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_async(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # ── S3 / Medallion paths ────────────────────────────────────────────
    bronze_bucket: str = Field(default="ai-catalog-bronze-dev", env="BRONZE_BUCKET")
    silver_bucket: str = Field(default="ai-catalog-silver-dev", env="SILVER_BUCKET")
    gold_bucket: str = Field(default="ai-catalog-gold-dev", env="GOLD_BUCKET")
    quarantine_prefix: str = Field(default="_quarantine", env="QUARANTINE_PREFIX")
    bronze_s3_paths: str = Field(default="", env="BRONZE_S3_PATHS")

    @property
    def bronze_paths_list(self) -> list[str]:
        if self.bronze_s3_paths:
            return [p.strip() for p in self.bronze_s3_paths.split(",") if p.strip()]
        return [f"s3://{self.bronze_bucket}/"]

    # ── AWS ─────────────────────────────────────────────────────────────
    aws_region: str = Field(default="us-east-1", env="AWS_REGION")
    aws_endpoint_url: str | None = Field(default=None, env="AWS_ENDPOINT_URL")
    bedrock_model_id: str = Field(
        default="anthropic.claude-3-5-haiku-20241022-v1:0",
        env="BEDROCK_MODEL_ID",
    )
    bedrock_embedding_model_id: str = Field(
        default="amazon.titan-embed-text-v1",
        env="BEDROCK_EMBEDDING_MODEL_ID",
    )

    # ── Agent graph ─────────────────────────────────────────────────────
    agent_max_retries: int = Field(default=3, env="AGENT_MAX_RETRIES")
    quality_threshold: float = Field(default=0.95, env="QUALITY_THRESHOLD")
    graph_timeout_seconds: int = Field(default=600, env="GRAPH_TIMEOUT_SECONDS")

    # ── OpenTelemetry ───────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = Field(default="", env="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_service_name: str = Field(default="ai-data-catalog-agent", env="OTEL_SERVICE_NAME")

    # ── Great Expectations ──────────────────────────────────────────────
    gx_expectation_suite: str = Field(default="default_suite", env="GX_EXPECTATION_SUITE")

    # ── dbt ─────────────────────────────────────────────────────────────
    dbt_target: str = Field(default="dev", env="DBT_TARGET")
    dbt_profiles_dir: str = Field(default="./profiles", env="DBT_PROFILES_DIR")

    # ── File size limits ────────────────────────────────────────────────
    max_file_size_bytes: int = Field(default=500 * 1024 * 1024, env="MAX_FILE_SIZE_BYTES")  # 500 MB
    supported_formats: tuple[str, ...] = ("json", "csv", "parquet", "avro", "pdf", "txt")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

settings = Settings()
