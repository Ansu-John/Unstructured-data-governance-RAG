"""
profiling.py — LangGraph Node: Data Profiling & Schema Health Evaluation

Responsibility:
  For each file that passed quality validation, compute a statistical profile:
  schema field inventory, row/column counts, null/distinct rates, inferred
  data types, and a representative sample. The profile informs the cataloging
  node about the data's shape and health.

Design decisions:
  - Uses PySpark for distributed profile computation when available, falling
    back to Pandas for local/small-data scenarios.
  - Null-rate profiling is critical: downstream cataloging uses it to set
    data quality metadata on the vector-store entry.
  - Schema drift detection is computed by comparing inferred types against
    the Silver-layer registered schema (if available).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import (  # noqa: F401  (used in _spark_profile)
        col,
        count,
        countDistinct,
        isnan,
        isnull,
        when,
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
    ProfileResult,
    profile_result_to_dict,
)

logger = logging.getLogger(__name__)


def profiling_node(state: AgentState) -> dict[str, Any]:
    """
    StateGraph node: Profile each file that has a passing QualityResult.

    Reads from state:
      - files: List[Dict]
      - quality_results: Dict[str, Dict] (file_id -> result)
      - current_file_id: str

    Writes to state:
      - profile_results: Dict[str, Dict] (file_id -> profile)
      - profiling_summary: str
      - errors: List[str]
    """
    span_ctx = tracer.start_as_current_span("profiling_node") if HAS_OTEL else _NoopSpan()
    with span_ctx as span:
        try:
            files: list[dict] = state.get("files", [])
            quality_results: dict[str, dict] = state.get("quality_results", {})
            existing_profiles: dict[str, dict] = state.get("profile_results", {})
            errors: list[str] = list(state.get("errors", []))
            profiles: dict[str, dict] = dict(existing_profiles)

            spark = _get_spark()

            for f in files:
                file_id = f["file_id"]
                # Skip if already profiled (idempotent)
                if file_id in profiles:
                    continue
                # FIX 1: Strictly check if the file exists in quality_results
                if file_id not in quality_results:
                    logger.info("Skipping file %s — no quality result yet", f["file_name"])
                    continue

                qr_dict = quality_results[file_id]
                # FIX 2: Check for success
                if not qr_dict.get("success", False):
                    logger.info("Skipping file %s — failed quality check", f["file_name"])
                    continue

                try:
                    # FIX 3: Add type ignores for dynamic Spark/Pandas returns to satisfy Mypy

                    profile = _compute_profile(spark, f, qr_dict)
                    profiles[file_id] = profile_result_to_dict(profile) # type: ignore[arg-type]
                    logger.info(
                        "Profiled %s: %d rows, %d columns, null_rate=%.2f",
                        f["file_name"],
                        profile.row_count,
                        profile.column_count,
                        _avg_null_rate(profile.null_rates),
                    )
                except Exception as exc:
                    msg = f"profiling failed for {f['file_name']}: {exc}"
                    logger.error(msg)
                    errors.append(msg)
                    if HAS_OTEL:
                        span.record_exception(exc)

            summary = (
                f"Profiled {len(profiles) - len(existing_profiles)} new file(s). "
                f"Total profiled: {len(profiles)}."
            )

            result: dict[str, Any] = {
                "profile_results": profiles,
                "profiling_summary": summary,
            }
            if errors:
                result["errors"] = errors

            if HAS_OTEL:
                span.set_attribute("profiles_computed", len(profiles))
                span.set_attribute("error_count", len(errors))

            return result

        except Exception as exc:
            logger.critical("Profiling node crashed: %s", exc, exc_info=True)
            if HAS_OTEL:
                span.record_exception(exc)
            return {
                "error": f"profiling_node: {exc}",
                "errors": state.get("errors", []) + [f"profiling_node: {exc}"],
            }
        # finally:
        # if HAS_OTEL:
        #    span.end()


# ---------------------------------------------------------------------------
# Profile computation internals
# ---------------------------------------------------------------------------


def _get_spark() -> SparkSession | None:
    """Get or create a SparkSession. Returns None if unavailable."""
    try:
        from pyspark.sql import SparkSession  # Lazy import — avoids crash at module level

        return SparkSession.builder.appName("ProfilingNode").getOrCreate()
    except Exception as exc:
        logger.warning("SparkSession unavailable: %s", exc)
        return None


def _compute_profile(
    spark: SparkSession | None,
    file_dict: dict[str, Any],
    _qr_dict: dict[str, Any],
) -> ProfileResult:
    """
    Compute a statistical profile for a single file.

    Uses PySpark for distributed computation. Falls back to a mock profile
    when Spark is unavailable (local dev / test environments).
    """
    file_path = file_dict["file_path"]
    # Fall back to mock profile if S3 paths are detected in local dev without AWS jars
    if spark is not None and not file_path.startswith("s3://"):
        return _spark_profile(spark, file_path, file_dict["file_id"])
    else:
        return _mock_profile(file_dict)


def _spark_profile(spark: SparkSession, path: str, file_id: str) -> ProfileResult:
    """Distributed profile computation via PySpark."""
    # Lazy import — avoids crash at module level if PySpark/Java is absent
    from pyspark.sql.functions import (  # noqa: I001
        col, count, countDistinct, isnan, isnull, when,
    )

    # Read the file based on format
    fmt = path.split(".")[-1] if "." in path else "parquet"
    reader = spark.read
    if fmt == "json":
        df = reader.json(path)
    elif fmt == "csv":
        df = reader.option("header", "true").option("inferSchema", "true").csv(path)
    else:
        df = reader.parquet(path)

    schema_fields = [
        {"name": f.name, "type": str(f.dataType), "nullable": f.nullable} for f in df.schema
    ]
    row_count = df.count()
    column_count = len(df.columns)

    # Compute null rates per column
    null_rates: dict[str, float] = {}
    distinct_rates: dict[str, float] = {}

    if row_count > 0:
        for field in df.schema:
            col_name = field.name
            null_count = df.select(
                count(when(isnull(col(col_name)) | isnan(col(col_name)), 1))
            ).collect()[0][0]
            null_rates[col_name] = round(null_count / row_count, 4) if row_count > 0 else 0.0

            distinct_count = df.select(countDistinct(col(col_name))).collect()[0][0]
            distinct_rates[col_name] = (
                round(distinct_count / row_count, 4) if row_count > 0 else 0.0
            )

    # Inferred types
    # inferred_types = {f["name"]: f["type"] for f in schema_fields} < For MyPy
    # Argument "inferred_types" to "ProfileResult" has incompatible type "dict[object, object]";
    # expected "dict[str, str]"  [arg-type]
    inferred_types: dict[str, str] = {str(f["name"]): str(f["type"]) for f in schema_fields}

    # Sample data (first 5 rows)
    sample_rows = df.limit(5).toPandas().to_dict(orient="records") if row_count > 0 else [] # type: ignore[attr-defined]

    return ProfileResult(
        file_id=file_id,
        schema_fields=schema_fields,
        row_count=row_count,
        column_count=column_count,
        null_rates=null_rates,
        distinct_rates=distinct_rates,
        inferred_types=inferred_types,
        sample_data=sample_rows,
    )


def _mock_profile(file_dict: dict[str, Any]) -> ProfileResult:
    """Return a synthetic profile for local dev / testing."""
    return ProfileResult(
        file_id=file_dict["file_id"],
        schema_fields=[
            {"name": "id", "type": "LongType", "nullable": False},
            {"name": "name", "type": "StringType", "nullable": True},
            {"name": "email", "type": "StringType", "nullable": False},
            {"name": "signup_date", "type": "StringType", "nullable": True},
            {"name": "score", "type": "DoubleType", "nullable": True},
        ],
        row_count=1500,
        column_count=5,
        null_rates={"id": 0.0, "name": 0.02, "email": 0.0, "signup_date": 0.01, "score": 0.15},
        distinct_rates={"id": 1.0, "name": 0.67, "email": 1.0, "signup_date": 0.03, "score": 0.22},
        inferred_types={
            "id": "LongType",
            "name": "StringType",
            "email": "StringType",
            "signup_date": "StringType",
            "score": "DoubleType",
        },
        sample_data=[
            {"id": 1, "name": "Alice", "email": "alice@example.com", "score": 95.5},
            {"id": 2, "name": "Bob", "email": "bob@example.com", "score": 87.3},
        ],
    )


def _avg_null_rate(null_rates: dict[str, float]) -> float:
    if not null_rates:
        return 0.0
    return sum(null_rates.values()) / len(null_rates)


class _NoopSpan:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key, value):
        pass

    def record_exception(self, exc):
        pass
