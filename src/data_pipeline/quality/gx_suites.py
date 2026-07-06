"""
gx_suites.py — Enterprise Data Quality Runtime

PySpark execution engine that integrates Great Expectations as an in-memory
validation layer within the Medallion Architecture. Reads datasets from the
Bronze (raw) layer, executes configurable expectation suites covering null
thresholds, type coercions, value boundaries, and referential integrity, then
routes clean data to Silver and quarantines failing partitions.

Architectural invariants:
  - Data NEVER reaches Silver without passing GX validation (fail-fast barrier).
  - Failing rows are isolated to s3://<bucket>/_quarantine/ for forensic replay.
  - Every run is logged to the catalog.quality_runs table for observability.

Usage (PySpark submit):
    spark-submit gx_suites.py \
        --bronge-path s3://ai-catalog-bronze-dev/crm/users/ \
        --silver-path s3://ai-catalog-silver-dev/crm/users/ \
        --quarantine-path s3://ai-catalog-bronze-dev/_quarantine/ \
        --suite-name crm_users_suite \
        --expectation-threshold 0.95
"""

from __future__ import annotations
import argparse
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
import great_expectations as gx
import great_expectations.expectations as gxe
from great_expectations.core.expectation_suite import ExpectationSuite
from great_expectations.data_context import EphemeralDataContext
from great_expectations.expectations.expectation_configuration import ExpectationConfiguration
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import current_timestamp, input_file_name, lit
from src.common.config import settings
import os

# Hard-disable OpenTelemetry exports so it doesn't hang on shutdown
os.environ["OTEL_TRACES_EXPORTER"] = "none"
os.environ["OTEL_SDK_DISABLED"] = "true"
# ---------------------------------------------------------------------------
# Logging & Telemetry bootstrap
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("gx_suites")

# Attempt OpenTelemetry integration — non-fatal if absent
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    tracer = trace.get_tracer(__name__)
    HAS_OTEL = True
except ImportError:
    tracer = None
    HAS_OTEL = False
    logger.info("OpenTelemetry not available — running without distributed tracing")


# ---------------------------------------------------------------------------
# Data quality result schema
# ---------------------------------------------------------------------------


@dataclass
class QualityRunResult:
    """Immutable result of a single quality validation run."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    asset_name: str = ""
    source_path: str = ""
    run_timestamp: str = ""
    success: bool = False
    score: float = 0.0
    threshold: float = 0.95
    total_expectations: int = 0
    failed_expectations: int = 0
    validation_json: dict = field(default_factory=dict)
    quarantine_path: str = ""
    execution_secs: float = 0.0
    error_message: str = ""


# ---------------------------------------------------------------------------
# Core validation engine
# ---------------------------------------------------------------------------


class GreatExpectationsValidator:
    """
    Production-grade GX validator that operates entirely in-memory (Ephemeral
    Data Context) and is designed to be embedded inside a PySpark job.

    Key design decisions:
      - EphemeralDataContext avoids the need for a GX deployment / file store.
      - Expectation suites are defined programmatically (not YAML) so they can
        be version-controlled alongside the pipeline codebase.
      - RuntimeBatchRequest accepts a Spark DataFrame directly, eliminating
        unnecessary I/O round-trips.
    """

    # ── Pre-defined expectation catalog ──────────────────────────────────
    # Each suite is a list of tuples: (expectation_type, kwargs, meta)
    SUITE_REGISTRY: dict[str, list[tuple[str, dict, dict]]] = {
        "crm_users_suite": [
            ("expect_column_values_to_not_be_null", {"column": "id"}, {"priority": "critical"}),
            (
                "expect_column_values_to_be_of_type",
                {"column": "id", "type_": "LongType"},
                {"priority": "critical"},
            ),
            ("expect_column_values_to_not_be_null", {"column": "email"}, {"priority": "critical"}),
            (
                "expect_column_values_to_match_regex",
                {"column": "email", "regex": r"^[^@\s]+@[^@\s]+\.[^@\s]+$"},
                {"priority": "high"},
            ),
            (
                "expect_column_values_to_be_between",
                {"column": "score", "min_value": 0.0, "max_value": 100.0},
                {"priority": "high"},
            ),
            ("expect_column_values_to_not_be_null", {"column": "name"}, {"priority": "medium"}),
            ("expect_column_values_to_be_unique", {"column": "id"}, {"priority": "critical"}),
            ("expect_column_values_to_be_unique", {"column": "email"}, {"priority": "high"}),
        ],
        "inventory_suite": [
            ("expect_column_values_to_not_be_null", {"column": "sku"}, {"priority": "critical"}),
            ("expect_column_values_to_be_unique", {"column": "sku"}, {"priority": "critical"}),
            (
                "expect_column_values_to_be_between",
                {"column": "quantity", "min_value": 0},
                {"priority": "high"},
            ),
            (
                "expect_column_values_to_be_between",
                {"column": "unit_price", "min_value": 0.01},
                {"priority": "high"},
            ),
        ],
        "default_suite": [
            ("expect_column_values_to_not_be_null", {"column": "id"}, {"priority": "critical"}),
            ("expect_column_to_exist", {"column": "id"}, {"priority": "critical"}),
            (
                "expect_column_values_to_be_of_type",
                {"column": "id", "type_": "LongType"},
                {"priority": "high"},
            ),
        ],
    }

    def __init__(
        self,
        suite_name: str = "default_suite",
        expectation_threshold: float = 0.95,
        quarantine_base_path: str = "",
    ):
        if suite_name not in self.SUITE_REGISTRY:
            logger.warning(
                "Suite '%s' not found in registry. Falling back to 'default_suite'. "
                "Available suites: %s",
                suite_name,
                list(self.SUITE_REGISTRY.keys()),
            )
            suite_name = "default_suite"

        self.suite_name = suite_name
        self.expectation_threshold = expectation_threshold
        self.quarantine_base_path = quarantine_base_path
        self._context: EphemeralDataContext | None = None

        # Telemetry
        self._tracer = tracer

    # ── Public API ───────────────────────────────────────────────────────

    def validate_dataframe(
        self,
        df: DataFrame,
        asset_name: str,
        source_path: str,
    ) -> QualityRunResult:
        """
        Execute the expectation suite against a PySpark DataFrame.

        Returns a QualityRunResult with pass/fail decision derived from the
        configured threshold. The caller is responsible for quarantine routing.
        """
        result = QualityRunResult(
            asset_name=asset_name,
            source_path=source_path,
            run_timestamp=datetime.now(UTC).isoformat(),
            threshold=self.expectation_threshold,
        )

        with self._start_span("validate_dataframe") as span:
            span.set_attribute("asset_name", asset_name)
            span.set_attribute("suite_name", self.suite_name)
            span.set_attribute("threshold", self.expectation_threshold)

            t0 = time.perf_counter()
            try:
                self._ensure_context(df, asset_name)
                expectations = self.SUITE_REGISTRY[self.suite_name]

                # 1. Register the Spark Data Asset
                data_source = self._context.data_sources.add_spark(name=f"{asset_name}_src")
                data_asset = data_source.add_dataframe_asset(name=asset_name)
                batch_def = data_asset.add_batch_definition_whole_dataframe("batch_def")

                # 2. Retrieve the pre-registered suite
                suite = self._context.suites.get(self.suite_name)

                # 3. Create a Validation Definition (maps the data to the suite)
                val_def = gx.ValidationDefinition(
                    name=f"{asset_name}_validation",
                    data=batch_def,
                    suite=suite,
                )
                self._context.validation_definitions.add(val_def)

                # 4. Create and run the Checkpoint
                checkpoint = gx.Checkpoint(
                    name=f"{asset_name}_checkpoint",
                    validation_definitions=[val_def],
                )
                self._context.checkpoints.add(checkpoint)

                # We pass the actual Spark DataFrame at runtime here
                checkpoint_result = checkpoint.run(batch_parameters={"dataframe": df})

                # 5. Collect results from the single checkpoint run
                n_failed = 0
                validation_results = []

                # In GX 1.0, results are stored in a dictionary of run_results
                for val_result in checkpoint_result.run_results.values():
                    for exp_result in val_result.results:
                        validation_results.append(exp_result)
                        if not exp_result.success:
                            n_failed += 1

                result.total_expectations = len(expectations)
                result.failed_expectations = n_failed
                result.score = 1.0 - (n_failed / len(expectations)) if expectations else 1.0
                result.success = result.score >= self.expectation_threshold
                result.validation_json = {
                    "suite": self.suite_name,
                    "expectations": [
                        {"type": e[0], "kwargs": e[1], "meta": e[2]} for e in expectations
                    ],
                    "results": [
                        r.to_json_dict() if hasattr(r, "to_json_dict") else str(r)
                        for r in validation_results
                    ],
                }

                logger.info(
                    "Validation complete: asset=%s score=%.4f threshold=%.4f success=%s "
                    "(%d/%d expectations passed)",
                    asset_name,
                    result.score,
                    result.threshold,
                    result.success,
                    result.total_expectations - result.failed_expectations,
                    result.total_expectations,
                )

            except Exception as exc:
                result.success = False
                result.error_message = f"{type(exc).__name__}: {exc}"
                logger.error("Validation crashed for %s: %s", asset_name, result.error_message)
                span.record_exception(exc)

            finally:
                result.execution_secs = time.perf_counter() - t0
                span.set_attribute("execution_secs", result.execution_secs)
                span.set_attribute("success", result.success)

        return result

    def quarantine_failing_rows(
        self,
        df: DataFrame,
        run_result: QualityRunResult,
        _spark: SparkSession,
    ) -> str:
        """
        Isolate rows that failed validation into the quarantine zone and
        return the quarantine S3 path.

        The quarantine path follows a forensic-friendly structure:
            _quarantine/<asset_name>/run_id=<run_id>/
        """
        if not self.quarantine_base_path:
            logger.warning("No quarantine_base_path set — skipping quarantine write")
            return ""

        q_path = (
            f"{self.quarantine_base_path.rstrip('/')}/"
            f"{run_result.asset_name}/"
            f"run_id={run_result.run_id}/"
        )

        try:
            # Add metadata columns for forensic traceability
            q_df = df.withColumn("_quarantine_run_id", lit(run_result.run_id))
            q_df = q_df.withColumn("_quarantine_timestamp", current_timestamp())
            q_df = q_df.withColumn("_quarantine_score", lit(run_result.score))
            q_df = q_df.withColumn(
                "_quarantine_reason", lit(run_result.error_message or "threshold_failure")
            )

            q_df.write.mode("overwrite").parquet(q_path)
            logger.info("Quarantined %d rows to %s", df.count(), q_path)
        except Exception as exc:
            logger.error("Failed to write quarantine for %s: %s", run_result.asset_name, exc)
            raise

        return q_path

    # ── Private helpers ──────────────────────────────────────────────────

    def _ensure_context(self, _df: DataFrame, _asset_name: str) -> None:
        """Create or reuse the EphemeralDataContext."""
        if self._context is not None:
            return
        self._context = gx.get_context(mode="ephemeral")

        # Register the expectation suite (GX 1.0 API)
        suite = ExpectationSuite(name=self.suite_name)
        expectations = self.SUITE_REGISTRY[self.suite_name]

        for e in expectations:
            exp_name = e[0]   # e.g., "expect_column_values_to_not_be_null"
            kwargs = e[1]     # e.g., {"column": "id"}
            meta = e[2]       # e.g., {"priority": "critical"}

            # Convert snake_case string to CamelCase class name
            # "expect_column_values_to_not_be_null" -> "ExpectColumnValuesToNotBeNull"
            class_name = "".join(word.capitalize() for word in exp_name.split("_"))

            # Fetch the actual expectation class dynamically from the gxe module
            ExpectationClass = getattr(gxe, class_name)

            # Instantiate the object and add it to the suite
            suite.add_expectation(ExpectationClass(**kwargs, meta=meta))

        # Explicitly add the suite to the context
        self._context.suites.add(suite)

        logger.info(
            "Initialized ephemeral GX context with suite '%s' (%d expectations)",
            self.suite_name,
            len(expectations),
        )

    def _start_span(self, name: str):
        if self._tracer:
            return self._tracer.start_as_current_span(name)
        return _NoopSpan()


class _NoopSpan:
    """Context manager that does nothing — used when OTEL is absent."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key, value):
        pass

    def record_exception(self, exc):
        pass


# ---------------------------------------------------------------------------
# Pipeline entrypoint
# ---------------------------------------------------------------------------


def build_spark_session(app_name: str = "DataQualityValidator") -> SparkSession:
    """Build a SparkSession configured for local dev or EMR Serverless."""
    # 1. Base Builder: Your optimized EMR configs + the S3 Drivers
    builder = (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.adaptive.advisoryPartitionSizeInBytes", "64MB")
        .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer")
        .config("spark.sql.legacy.parquet.datetimeRebaseModeInWrite", "CORRECTED")
        # REQUIRED FOR LOCAL: Download the AWS and Hadoop JARs
        .config(
            "spark.jars.packages",
            "org.apache.hadoop:hadoop-aws:3.3.4,com.amazonaws:aws-java-sdk-bundle:1.12.262",
        )
        # REQUIRED FOR S3 INTERACTION: Map both s3:// and s3a:// to the modern driver
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
    )

    # 2. LocalStack Overrides: Only applied if the .env file provides a URL
    if settings.aws_endpoint_url:
        builder = (
            builder.config("spark.hadoop.fs.s3a.endpoint", settings.aws_endpoint_url)
            .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
            .config("spark.hadoop.fs.s3a.access.key", "LOCAL_DEV_AKID")
            .config("spark.hadoop.fs.s3a.secret.key", "LOCAL_DEV_SAK")
        )

    return builder.getOrCreate()


def get_latest_bronze_partitions(
    spark: SparkSession,
    bronze_base_path: str,
) -> list[tuple[str, str]]:
    """
    Scan the Bronze layer's Hive-style partitioning and return the most recent
    partition for each source/object pair.

    Returns: list of (full_s3_path, asset_name) tuples.
    """
    try:
        bronze_path = bronze_base_path.rstrip("/") + "/*/*/*/*"
        try:
            df = spark.read.parquet(bronze_path)
            df.head(1)
        except Exception as e:
            error_msg = str(e)
            if "CANNOT_READ_FILE_FOOTER" in error_msg or "Parquet" in error_msg:
                print(f"Parquet read failed. Falling back to JSON for path: {bronze_path}")
                df = spark.read.json(bronze_path)
            else:
                raise e
        paths = df.select(input_file_name().alias("path")).distinct().collect()
        # Group by the object name (one level above year=)
        partitions: dict[str, str] = {}
        for row in paths:
            path = row["path"]
            segments = path.split("/")
            for i, seg in enumerate(segments):
                if seg.startswith("year="):
                    object_name = segments[i - 1] if i > 0 else "unknown"
                    # Keep the most recent (max path lexicographically = newest)
                    base = "/".join(segments[: i + 3])  # up to day=DD
                    if object_name not in partitions or base > partitions[object_name]:
                        partitions[object_name] = base
        return [(v, k) for k, v in partitions.items()]
    except Exception as exc:
        logger.warning("No readable partitions at %s: %s", bronze_base_path, exc)
        return []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enterprise Data Quality Validator — Great Expectations + PySpark",
    )
    parser.add_argument("--bronze-path", required=True, help="S3 prefix for Bronze layer input")
    parser.add_argument("--silver-path", required=True, help="S3 prefix for Silver layer output")
    parser.add_argument("--quarantine-path", default="", help="S3 prefix for quarantine zone")
    parser.add_argument("--suite-name", default="default_suite", help="Expectation suite name")
    parser.add_argument(
        "--expectation-threshold", type=float, default=0.95, help="Pass threshold [0-1]"
    )
    parser.add_argument("--asset-name", default="", help="Override auto-detected asset name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Initialize OpenTelemetry
    from src.common.telemetry import init_telemetry

    init_telemetry(
        service_name="ai-catalog-quality",
        environment=os.environ.get("ENVIRONMENT", "local"),
    )

    logger.info("=" * 72)
    logger.info("Data Quality Validator starting")
    logger.info("  Bronze path:       %s", args.bronze_path)
    logger.info("  Silver path:       %s", args.silver_path)
    logger.info("  Suite:             %s", args.suite_name)
    logger.info("  Threshold:         %.2f", args.expectation_threshold)
    logger.info("=" * 72)

    spark = build_spark_session()
    validator = GreatExpectationsValidator(
        suite_name=args.suite_name,
        expectation_threshold=args.expectation_threshold,
        quarantine_base_path=args.quarantine_path,
    )

    # Discover Bronze partitions
    partitions = get_latest_bronze_partitions(spark, args.bronze_path)
    if not partitions:
        logger.info("No Bronze partitions found — reading entire prefix as single asset")
        partitions = [(args.bronze_path.rstrip("/"), args.asset_name or "default")]

    all_passed = True
    for source_path, asset_name in partitions:
        logger.info("Processing asset '%s' from %s", asset_name, source_path)

        # 1. Read
        try:
            df = spark.read.parquet(source_path)
            df.head(1)
        except Exception as e:
            error_msg = str(e)
            # If it fails because there is no Parquet footer, fall back to JSON (DEV behavior)
            if "CANNOT_READ_FILE_FOOTER" in error_msg or "Parquet" in error_msg:
                print(f"Parquet read failed. Falling back to JSON for path: {source_path}")
                df = spark.read.json(source_path)
            else:
                # If it's a different error (like path not found), raise it normally
                raise e

        # 2. Validate
        run_result = validator.validate_dataframe(df, asset_name, source_path)
        logger.info(
            "Result: success=%s score=%.4f (threshold=%.4f) exec_secs=%.2f",
            run_result.success,
            run_result.score,
            run_result.threshold,
            run_result.execution_secs,
        )

        # 3. Route: Silver (pass) or Quarantine (fail)
        if run_result.success:
            silver_dest = f"{args.silver_path.rstrip('/')}/{asset_name}/"
            df.write.mode("append").parquet(silver_dest)
            logger.info("  ✓ Written to Silver: %s", silver_dest)
        else:
            all_passed = False
            q_path = validator.quarantine_failing_rows(df, run_result, spark)
            run_result.quarantine_path = q_path
            logger.warning("  ✗ Data quarantined to: %s", q_path)

        # 4. Persist run result to catalog (if DB reachable)
        _persist_run_to_db(run_result)
    if not all_passed:
        logger.error("One or more assets FAILED validation. See quarantine for details.")
        spark.stop()  # <-- ADD THIS HERE to cleanly close Spark on failure
        raise SystemExit(1)

    logger.info("All assets passed quality validation.")
    spark.stop()  # <-- ADD THIS HERE to cleanly close Spark on success

def _persist_run_to_db(result: QualityRunResult) -> None:
    """Best-effort write of the run result to the catalog quality_runs table."""
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5433")),
            dbname=os.environ.get("DB_NAME", "postgres"),
            user=os.environ.get("DB_USER", "postgres"),
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO catalog.quality_runs
                    (run_id, asset_name, source_path, run_timestamp, success,
                     score, threshold, total_expectations, failed_expectations,
                     validation_json, quarantine_path, execution_secs)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    result.run_id,
                    result.asset_name,
                    result.source_path,
                    result.run_timestamp,
                    result.success,
                    result.score,
                    result.threshold,
                    result.total_expectations,
                    result.failed_expectations,
                    json.dumps(result.validation_json),
                    result.quarantine_path,
                    result.execution_secs,
                ),
            )
            conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to persist run result to DB (non-fatal): %s", exc)


if __name__ == "__main__":
    main()
