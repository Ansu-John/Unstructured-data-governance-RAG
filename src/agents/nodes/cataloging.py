"""
cataloging.py — LangGraph Node: Vector Store Cataloging

Responsibility:
  For each profiled file, generate a rich metadata description using an LLM
  (Amazon Bedrock Claude 3.5 Sonnet), compute a vector embedding using Amazon
  Titan Embeddings, and persist the combined catalog entry to the pgvector
  store (catalog.data_assets).

Key design decisions:
  - LLM calls are wrapped with tenacity retry decorators for Bedrock
    rate-limit resilience (exponential backoff + jitter).
  - Embeddings are computed in batch where possible to minimize API calls.
  - Descriptions are generated from schema + sample data for zero-shot
    cataloging — no human labeling required.
  - The node is idempotent: re-running on the same file_id updates rather
    than duplicates the catalog entry.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

try:
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    HAS_OTEL = True
except ImportError:
    tracer = None # type: ignore[assignment]
    HAS_OTEL = False

from src.agents.state import (
    AgentState,
    CatalogEntry,
    DataAssetType,
    catalog_entry_to_dict,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry policy for Bedrock LLM calls
# ---------------------------------------------------------------------------

# Common transient exceptions from Bedrock / boto3
BEDROCK_RETRYABLE = (
    ConnectionError,
    TimeoutError,
    OSError,
)

try:
    from botocore.exceptions import (
        BotoCoreError,
        ClientError,
        ConnectionClosedError,
        EndpointConnectionError,
        ReadTimeoutError,
    )

    BEDROCK_RETRYABLE = BEDROCK_RETRYABLE + (
        ClientError,
        ConnectionClosedError,
        EndpointConnectionError,
        ReadTimeoutError,
        BotoCoreError,
    ) # type: ignore[assignment]
except ImportError:
    pass

bedrock_retry = retry(
    retry=retry_if_exception_type(BEDROCK_RETRYABLE),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=1, max=60, jitter=2),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


# ---------------------------------------------------------------------------
# Cataloging Node
# ---------------------------------------------------------------------------


def cataloging_node(state: AgentState) -> dict[str, Any]:
    """
    StateGraph node: Generate catalog entries and write them to pgvector.

    Reads from state:
      - files: List[Dict]
      - profile_results: Dict[str, Dict]
      - quality_results: Dict[str, Dict]
      - existing catalog_entries: List[Dict]

    Writes to state:
      - catalog_entries: List[Dict] — appended with new entries
      - cataloging_summary: str
      - errors: List[str]
    """
    span_ctx = tracer.start_as_current_span("cataloging_node") if HAS_OTEL else _NoopSpan()

    with span_ctx as span:
        try:
            files: list[dict] = state.get("files", [])
            profiles: dict[str, dict] = state.get("profile_results", {})
            quality_results: dict[str, dict] = state.get("quality_results", {})
            existing_entries: list[dict] = list(state.get("catalog_entries", []))
            errors: list[str] = list(state.get("errors", []))
            existing_file_ids = {e["file_id"] for e in existing_entries}

            new_entries: list[dict] = []

            for f in files:
                file_id = f["file_id"]
                # Skip if already cataloged (idempotent)
                if file_id in existing_file_ids:
                    continue

                profile = profiles.get(file_id)
                qr = quality_results.get(file_id)

                if profile is None:
                    logger.info("Skipping %s — no profile available", f["file_name"])
                    errors.append(f"Skipped {f['file_name']}: no profile")
                    continue

                try:
                    entry = _build_catalog_entry(f, profile, qr)
                    _persist_to_vector_store(entry)
                    new_entries.append(catalog_entry_to_dict(entry))
                    logger.info(
                        "Cataloged '%s' -> vector store (score=%.2f, rows=%d)",
                        entry.asset_name,
                        entry.quality_score,
                        entry.row_count,
                    )
                except Exception as exc:
                    msg = f"cataloging failed for {f['file_name']}: {exc}"
                    logger.error(msg)
                    errors.append(msg)
                    if HAS_OTEL:
                        span.record_exception(exc)

            all_entries = existing_entries + new_entries

            summary = (
                f"Cataloged {len(new_entries)} new asset(s) to vector store. "
                f"Total cataloged: {len(all_entries)}."
            )

            result: dict[str, Any] = {
                "catalog_entries": all_entries,
                "cataloging_summary": summary,
            }
            if errors:
                result["errors"] = errors

            if HAS_OTEL:
                span.set_attribute("entries_created", len(new_entries))
                span.set_attribute("total_entries", len(all_entries))
                span.set_attribute("error_count", len(errors))

            return result

        except Exception as exc:
            logger.critical("Cataloging node crashed: %s", exc, exc_info=True)
            if HAS_OTEL:
                span.record_exception(exc)
            return {
                "error": f"cataloging_node: {exc}",
                "errors": state.get("errors", []) + [f"cataloging_node: {exc}"],
            }
        # finally:
        # if HAS_OTEL:
        # span.end()


# ---------------------------------------------------------------------------
# Entry construction
# ---------------------------------------------------------------------------


def _build_catalog_entry(
    file_dict: dict[str, Any],
    profile_dict: dict[str, Any],
    quality_dict: dict[str, Any] | None,
) -> CatalogEntry:
    """Build a CatalogEntry from the file, profile, and quality data."""

    description = _generate_description(file_dict, profile_dict)

    embedding = _compute_embedding(description)

    return CatalogEntry(
        file_id=file_dict["file_id"],
        asset_name=file_dict.get("file_name", "unknown"),
        asset_type=_infer_asset_type(file_dict.get("file_format", "")),
        source_system=file_dict.get("source_system", "unknown"),
        schema_name=file_dict.get("source_system", ""),
        table_name=file_dict.get("object_type", ""),
        file_path=file_dict.get("file_path", ""),
        description=description,
        tags=_infer_tags(profile_dict, file_dict),
        quality_score=quality_dict.get("score", 0.0) if quality_dict else 0.0,
        row_count=profile_dict.get("row_count", 0),
        partition_count=1,
        embedding=embedding,
        metadata_json={
            "source_system": file_dict.get("source_system", ""),
            "object_type": file_dict.get("object_type", ""),
            "file_format": file_dict.get("file_format", ""),
            "partition_date": file_dict.get("partition_date", ""),
            "file_size_bytes": file_dict.get("file_size_bytes", 0),
            "column_count": profile_dict.get("column_count", 0),
            "null_rates": profile_dict.get("null_rates", {}),
            "distinct_rates": profile_dict.get("distinct_rates", {}),
            "inferred_types": profile_dict.get("inferred_types", {}),
        },
    )


# ---------------------------------------------------------------------------
# LLM-powered description generation
# ---------------------------------------------------------------------------


@bedrock_retry
def _generate_description(file_dict: dict[str, Any], profile_dict: dict[str, Any]) -> str:
    """
    Generate a rich, human-readable data asset description using Amazon Bedrock
    (Claude 3.5 Sonnet).

    Falls back to a template-based description if Bedrock is unavailable.
    """
    try:
        import boto3

        # Import your centralized settings object
        from src.common.config import settings

        bedrock = boto3.Session().client(
            "bedrock-runtime",
            region_name=settings.aws_region,
        )

        schema_context = json.dumps(profile_dict.get("schema_fields", []), indent=2)
        sample_context = json.dumps(profile_dict.get("sample_data", [])[:3], indent=2)

        prompt = (
            "You are an enterprise data cataloging assistant. "
            "Generate a concise, informative description for the following data asset.\n\n"
            f"Asset name: {file_dict.get('file_name', 'unknown')}\n"
            f"Source system: {file_dict.get('source_system', 'unknown')}\n"
            f"Object type: {file_dict.get('object_type', 'unknown')}\n"
            f"Row count: {profile_dict.get('row_count', 0)}\n"
            f"Schema:\n{schema_context}\n"
            f"Sample rows:\n{sample_context}\n\n"
            "Description (2-3 sentences covering: what this data represents, "
            "key columns, and data quality considerations):"
        )

        response = bedrock.invoke_model(
            modelId=settings.bedrock_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 256,
                    "temperature": 0.3,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        )

        response_body = json.loads(response["body"].read())
        description = response_body["content"][0]["text"].strip()
        logger.info("LLM description generated (%d chars)", len(description))
        return description

    except Exception as exc:
        logger.warning("Bedrock LLM unavailable for description generation: %s", exc)
        return _fallback_description(file_dict, profile_dict)


def _fallback_description(file_dict: dict[str, Any], profile_dict: dict[str, Any]) -> str:
    """Template-based fallback when Bedrock is not reachable."""
    fields = profile_dict.get("schema_fields", [])
    field_names = [f.get("name", "?") for f in fields]
    return (
        f"Data asset '{file_dict.get('file_name', 'unknown')}' from "
        f"source system '{file_dict.get('source_system', 'unknown')}'. "
        f"Contains {profile_dict.get('row_count', 0)} rows across "
        f"{len(field_names)} columns: {', '.join(field_names[:8])}"
        f"{'...' if len(field_names) > 8 else ''}. "
        f"Format: {file_dict.get('file_format', 'unknown')}."
    )


# ---------------------------------------------------------------------------
# Embedding computation (Amazon Titan Embeddings)
# ---------------------------------------------------------------------------


@bedrock_retry
def _compute_embedding(text: str) -> list[float]:
    """
    Compute a 1536-dimension vector embedding using Amazon Titan Embeddings
    (v2) via Bedrock.

    Falls back to a zero-vector of the correct dimension if Bedrock is
    unavailable.
    """
    try:
        import boto3

        # Import your centralized settings object
        from src.common.config import settings

        bedrock = boto3.Session().client(
            "bedrock-runtime",
            region_name=settings.aws_region,
        )

        response = bedrock.invoke_model(
            modelId=settings.bedrock_embedding_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "inputText": text
                }
            ),
        )

        response_body = json.loads(response["body"].read())
        embedding = response_body["embedding"]
        logger.debug("Embedding computed (dim=%d)", len(embedding))
        return embedding

    except Exception as exc:
        logger.warning("Bedrock Titan Embeddings unavailable: %s", exc)
        return [0.0] * 1536


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_asset_type(file_format: str) -> DataAssetType:
    mapping = {
        "json": DataAssetType.FILE,
        "csv": DataAssetType.FILE,
        "parquet": DataAssetType.TABLE,
        "avro": DataAssetType.TABLE,
        "pdf": DataAssetType.FILE,
        "txt": DataAssetType.FILE,
    }
    return mapping.get(file_format.lower(), DataAssetType.FILE)


def _infer_tags(profile_dict: dict[str, Any], file_dict: dict[str, Any]) -> list[str]:
    tags = []
    tags.append(file_dict.get("source_system", "unknown"))
    tags.append(file_dict.get("file_format", "unknown"))

    # Derive tags from null rates
    null_rates = profile_dict.get("null_rates", {})
    high_null_cols = [col for col, rate in null_rates.items() if rate > 0.3]
    if high_null_cols:
        tags.append("has_sparse_columns")

    return tags


# ---------------------------------------------------------------------------
# Vector store persistence
# ---------------------------------------------------------------------------


def _persist_to_vector_store(entry: CatalogEntry) -> None:
    """
    Write the catalog entry to the pgvector-backed data_assets table.

    Uses PostgreSQL + pgvector directly via psycopg2. The embedding column
    is cast to the vector type for index-backed ANN search.

    This is a best-effort write: failures are logged but not fatal.
    """
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5433")),
            dbname=os.environ.get("DB_NAME", "postgres"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", ""),
        )

        with conn.cursor() as cur:
            embedding_str = (
                f"[{','.join(str(v) for v in entry.embedding)}]" if entry.embedding else None
            )

            cur.execute(
                """
                INSERT INTO catalog.data_assets
                    (asset_name, asset_type, source_system, schema_name, table_name,
                     file_path, description, tags, quality_score, row_count,
                     partition_count, embedding, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::vector, %s)
                ON CONFLICT (file_path)
                DO UPDATE SET
                    quality_score = EXCLUDED.quality_score,
                    row_count = EXCLUDED.row_count,
                    embedding = EXCLUDED.embedding,
                    description = EXCLUDED.description,
                    tags = EXCLUDED.tags,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = NOW()
                """,
                (
                    entry.asset_name,
                    entry.asset_type.value,
                    entry.source_system,
                    entry.schema_name,
                    entry.table_name,
                    entry.file_path,
                    entry.description,
                    entry.tags,
                    entry.quality_score,
                    entry.row_count,
                    entry.partition_count,
                    embedding_str,
                    json.dumps(entry.metadata_json),
                ),
            )
            conn.commit()

        conn.close()
        logger.info("Persisted catalog entry '%s' to pgvector store", entry.asset_name)

    except Exception as exc:
        logger.error("Failed to persist catalog entry '%s': %s", entry.asset_name, exc)
        raise


class _NoopSpan:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key, value):
        pass

    def record_exception(self, exc):
        pass
