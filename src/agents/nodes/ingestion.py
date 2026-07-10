"""
ingestion.py — LangGraph Node: Bronze Layer Ingestion Scanner

Responsibility:
  Scan the Bronze S3 landing zone(s), discover new files matching the
  Hive-style partition pattern, create FileRecord entries for each, and
  update the AgentState with the discovered inventory.

OpenTelemetry:
  Wraps execution in a span tagged with the source system and file count.

Error handling:
  Catches and records S3 access failures, malformed paths, and schema
  mismatches without crashing the entire graph — failing files are marked
  FAILED and skipped by downstream nodes.
"""

from __future__ import annotations

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

# OpenTelemetry — best-effort import
try:
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    HAS_OTEL = True
except ImportError:
    tracer = None # type: ignore[assignment]
    HAS_OTEL = False

from src.agents.state import (
    AgentState,
    FileRecord,
    ProcessingStatus,
    file_record_to_dict,
)

logger = logging.getLogger(__name__)

# Default Bronze prefixes to scan
DEFAULT_BRONZE_PATHS = [
    "s3://ai-catalog-bronze-dev/",
]


def ingestion_node(state: AgentState) -> dict[str, Any]:
    """
    StateGraph node: Scan Bronze S3 paths for new files and populate
    the 'files' key in AgentState.

    Reads from state:
      - state.get("files", []) — existing files (for idempotent re-runs)

    Writes to state:
      - files: List[Dict] — updated file inventory
      - ingestion_summary: str — human-readable summary
      - errors: List[str] — any errors encountered
    """
    # 1. Rename the variable to clarify it is a context manager, not the span
    span_ctx = tracer.start_as_current_span("ingestion_node") if HAS_OTEL else _NoopSpan()

    # 2. Use 'as span' to capture the actual Span object
    with span_ctx as span:
        try:
            bronze_paths = _resolve_bronze_paths()
            discovered: list[FileRecord] = []
            errors: list[str] = []

            logger.info("Ingestion node scanning %d Bronze path(s)", len(bronze_paths))

            for base_path in bronze_paths:
                try:
                    files = _scan_s3_prefix(base_path)
                    discovered.extend(files)
                    logger.info("  Scanned %s: found %d file(s)", base_path, len(files))
                except Exception as exc:
                    msg = f"Failed to scan {base_path}: {exc}"
                    logger.error(msg)
                    errors.append(msg)
                    if HAS_OTEL:
                        span.record_exception(exc)

            # Merge with existing files (avoid duplicates on re-run)
            existing_paths = {f["file_path"] for f in state.get("files", [])}
            new_files = [
                file_record_to_dict(fr) for fr in discovered if fr.file_path not in existing_paths
            ]
            all_files = list(state.get("files", [])) + new_files

            summary = (
                f"Discovered {len(new_files)} new file(s) across "
                f"{len(bronze_paths)} Bronze path(s). "
                f"Total tracked: {len(all_files)}."
            )
            logger.info(summary)

            result: dict[str, Any] = {
                "files": all_files,
                "ingestion_summary": summary,
            }
            if errors:
                result["errors"] = state.get("errors", []) + errors

            if HAS_OTEL:
                span.set_attribute("files_discovered", len(new_files))
                span.set_attribute("total_files", len(all_files))
                span.set_attribute("error_count", len(errors))

            return result

        except Exception as exc:
            logger.critical("Ingestion node crashed: %s", exc, exc_info=True)
            if HAS_OTEL:
                span.record_exception(exc)
            return {
                "error": f"ingestion_node: {exc}",
                "errors": state.get("errors", []) + [f"ingestion_node: {exc}"],
            }
    # 4. REMOVED the `finally: span.end()` block.
    # The OpenTelemetry context manager handles ending the span automatically.
    #   finally:
    #       if HAS_OTEL:
    #           span.end()


# ---------------------------------------------------------------------------
# Retry policy for S3 operations
# ---------------------------------------------------------------------------

S3_RETRYABLE = (Exception,)

s3_retry = retry(
    retry=retry_if_exception_type(S3_RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=30, jitter=2),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_bronze_paths() -> list[str]:
    """Resolve Bronze S3 prefixes from env or fall back to defaults."""
    env_paths = os.environ.get("BRONZE_S3_PATHS", "")
    if env_paths:
        return [p.strip() for p in env_paths.split(",") if p.strip()]
    return DEFAULT_BRONZE_PATHS


@s3_retry
def _scan_s3_prefix(prefix: str) -> list[FileRecord]:
    """
    Simulate an S3 prefix scan.

    In production, this uses boto3 to list objects with the Hive partition
    pattern. In local dev, it reads from LocalStack or a mock.

    Returns a list of FileRecord objects.
    """
    records: list[FileRecord] = []

    try:
        import boto3

        # Import your centralized settings object
        from src.common.config import settings

        session = boto3.Session()
        s3 = session.client(
            "s3",
            endpoint_url=settings.aws_endpoint_url,  # LocalStack
            region_name=settings.aws_region,
        )

        # Parse bucket and prefix
        path = prefix.replace("s3://", "")
        bucket = path.split("/")[0]
        prefix_key = "/".join(path.split("/")[1:]) if "/" in path else ""

        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix_key)

        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Filter to data files only, skip _SUCCESS, _metadata, etc.
                if not _is_data_file(key):
                    continue

                # Parse Hive partition info
                source_system, object_type, partition_date = _parse_partition_path(key)

                records.append(
                    FileRecord(
                        file_path=f"s3://{bucket}/{key}",
                        file_name=key.split("/")[-1],
                        file_size_bytes=obj.get("Size", 0),
                        file_format=key.split(".")[-1] if "." in key else "unknown",
                        source_system=source_system,
                        object_type=object_type,
                        partition_date=partition_date,
                        status=ProcessingStatus.PENDING,
                    )
                )

    except ImportError:
        logger.warning("boto3 not available — using mock scan")
        records = _mock_scan(prefix)
    except Exception as exc:
        logger.error("S3 scan failed for %s: %s", prefix, exc)
        raise

    return records


def _is_data_file(key: str) -> bool:
    """Return True if the key represents a data file (not meta/directory/quarantine)."""
    if key.endswith("/"):
        return False
    basename = key.split("/")[-1]
    # Skip hidden files, metadata markers, and quarantine paths
    return (
        not basename.startswith("_")
        and not basename.startswith(".")
        and "/_quarantine/" not in key
    )


def _parse_partition_path(key: str) -> tuple[str, str, str]:
    """
    Extract source_system, object_type, and partition_date from a Hive-style
    S3 path: source_system/object_type/year=YYYY/month=MM/day=DD/file.ext
    """
    parts = key.split("/")
    source_system = "unknown"
    object_type = "unknown"
    partition_date = ""

    # Look for partition columns
    year = month = day = ""
    for p in parts:
        if p.startswith("year="):
            year = p.split("=")[1]
        elif p.startswith("month="):
            month = p.split("=")[1]
        elif p.startswith("day="):
            day = p.split("=")[1]

    if year and month and day:
        partition_date = f"{year}-{month}-{day}"

    # First non-partition segments are source/object
    non_partition = [p for p in parts if "=" not in p]
    if len(non_partition) >= 2:
        source_system = non_partition[0]
        object_type = non_partition[1]
    elif len(non_partition) == 1:
        source_system = non_partition[0]

    return source_system, object_type, partition_date


def _mock_scan(prefix: str) -> list[FileRecord]:
    """Return synthetic records for local development."""
    return [
        FileRecord(
            file_path=f"{prefix.rstrip('/')}/crm/users/year=2024/month=03/day=10/users_20240310.json",
            file_name="users_20240310.json",
            file_size_bytes=2048,
            file_format="json",
            source_system="crm",
            object_type="users",
            partition_date="2024-03-10",
            status=ProcessingStatus.PENDING,
        ),
        FileRecord(
            file_path=f"{prefix.rstrip('/')}/crm/users/year=2024/month=03/day=11/users_20240311.json",
            file_name="users_20240311.json",
            file_size_bytes=1890,
            file_format="json",
            source_system="crm",
            object_type="users",
            partition_date="2024-03-11",
            status=ProcessingStatus.PENDING,
        ),
    ]


class _NoopSpan:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key, value):
        pass

    def record_exception(self, exc):
        pass
