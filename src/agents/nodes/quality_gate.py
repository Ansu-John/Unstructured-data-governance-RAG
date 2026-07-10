"""
quality_gate.py — LangGraph Node: Fetch GX Quality Results from the Database

Responsibility:
  Read the latest Great Expectations validation results from the
  catalog.quality_runs table for each file discovered by the ingestion
  node and populate the state["quality_results"] dict so that the
  conditional quality_router can make per-file pass/quarantine decisions.

Integration:
  This node sits between "ingestion" and "profiling" in the graph:

      [START] → ingestion → fetch_quality_results → profiling → quality_router → ...

  Each file discovered by ingestion is matched to the most recent GX run
  by its S3 path prefix (the GX source_path is a prefix of the file's
  file_path).  If no matching quality run exists, the file is logged as
  "pending quality check" and will be skipped by profiling_node (which
  only profiles files with a quality result).

Design decisions:
  - Reads from catalog.quality_runs (written by gx_suites.py's _persist_run_to_db).
  - Matches by S3 path prefix, not by file_id (the GX pipeline assigns its own
    run_id and does not know about the LangGraph file_id).
  - Uses the MOST RECENT quality run per source_path (ORDER BY run_timestamp DESC
    LIMIT 1) so that repeated GX runs update the quality decision.
  - Merges with existing quality_results in state: if the database is unreachable
    (e.g., in test environments), the existing quality_results dict is preserved
    so that pre-populated fixtures or previously-fetched results are not lost.
  - The DB_PASSWORD env var is read from the ECS secret injection
    (Secrets Manager via the task definition).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.agents.state import AgentState, QualityResult, quality_result_to_dict

logger = logging.getLogger(__name__)


def fetch_quality_results_node(state: AgentState) -> dict[str, Any]:
    """
    StateGraph node: Fetch GX quality results from catalog.quality_runs
    and populate state["quality_results"].

    Reads from state:
      - files: List[Dict] — each entry must have at least "file_id" and "file_path".

    Writes to state:
      - quality_results: Dict[str, Dict] — maps file_id -> QualityResult dict.
        Only files with a matching GX run are included.

    Best-effort: database errors are logged but never crash the graph.
    """
    files: list[dict[str, Any]] = state.get("files", [])
    if not files:
        logger.info("No files to check quality for — skipping quality gate")
        return {"quality_results": {}}

    # Start with existing quality_results (e.g. from test fixtures or
    # from a previous graph run) so we never unintentionally clear them.
    quality_results: dict[str, dict[str, Any]] = dict(state.get("quality_results", {}))
    errors: list[str] = list(state.get("errors", []))

    try:
        import psycopg

        conn = psycopg.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5433")),
            dbname=os.environ.get("DB_NAME", "postgres"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", ""),
        )

        with conn.cursor() as cur:
            for f in files:
                file_id = f.get("file_id", "")
                file_path = f.get("file_path", "")
                file_name = f.get("file_name", "unknown")

                if not file_id or not file_path:
                    continue

                # Match the GX source_path (a prefix of our file_path).
                # The GX pipeline writes source_path like:
                #   s3://ai-catalog-bronze-dev/crm/users/year=2024/month=03/day=10
                # Our file_path is:
                #   s3://ai-catalog-bronze-dev/crm/users/year=2024/month=03/day=10/users_20240310.json
                # We match by checking if file_path starts with the GX source_path.
                # We use LEFT() instead of LIKE to avoid _ wildcard interpretation.
                # We use the most recent run per matching source_path.
                cur.execute(
                    """
                    SELECT DISTINCT ON (source_path)
                        run_id,
                        source_path,
                        success,
                        score,
                        threshold,
                        total_expectations,
                        failed_expectations,
                        validation_json,
                        quarantine_path,
                        execution_secs,
                        run_timestamp
                    FROM catalog.quality_runs
                    WHERE LEFT(%s, LENGTH(source_path)) = source_path
                    ORDER BY source_path, run_timestamp DESC
                    """,
                    (file_path,),
                )

                row = cur.fetchone()
                if row is None:
                    logger.info(
                        "No quality result found for %s (%s) — will skip profiling",
                        file_name,
                        file_path,
                    )
                    continue

                (
                    run_id,
                    source_path,
                    success,
                    score,
                    threshold,
                    total_expectations,
                    failed_expectations,
                    validation_json,
                    quarantine_path,
                    execution_secs,
                    run_timestamp,
                ) = row

                # Handle validation_json — it could be a dict or a string
                if isinstance(validation_json, str):
                    try:
                        validation_json = json.loads(validation_json)
                    except json.JSONDecodeError:
                        validation_json = {"raw": validation_json}

                qr = QualityResult(
                    file_id=file_id,
                    success=bool(success),
                    score=float(score or 0.0),
                    threshold=float(threshold or 0.95),
                    total_expectations=int(total_expectations or 0),
                    failed_expectations=int(failed_expectations or 0),
                    validation_json=validation_json or {},
                    quarantine_path=str(quarantine_path or ""),
                    execution_secs=float(execution_secs or 0.0),
                )
                quality_results[file_id] = quality_result_to_dict(qr)

                logger.info(
                    "Quality result for %s: score=%.4f threshold=%.4f success=%s "
                    "(run=%s, source_path=%s)",
                    file_name,
                    qr.score,
                    qr.threshold,
                    qr.success,
                    str(run_id)[:8],
                    source_path,
                )

        conn.close()

    except ImportError:
        logger.warning("psycopg not available — cannot fetch quality results from DB")
    except Exception as exc:
        msg = f"Failed to fetch quality results: {exc}"
        logger.error(msg)
        errors.append(msg)

    result: dict[str, Any] = {"quality_results": quality_results}
    if errors:
        result["errors"] = errors

    logger.info(
        "Quality gate complete: %d/%d files have quality results",
        len(quality_results),
        len(files),
    )
    return result
