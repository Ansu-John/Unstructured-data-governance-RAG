"""
state.py — AgentState TypedDict and Supporting Data Models

Defines the strictly-typed state graph schema used by the LangGraph
orchestrator. Every node in the graph reads from and writes to this state,
ensuring deterministic transitions, serializability for checkpointing, and
full observability of the execution trace.

Architectural invariants:
  - All state fields are JSON-serializable (required by Postgres checkpointer).
  - File-level granularity: each file through the pipeline is a distinct
    entry in the 'files' list, so partial failures don't corrupt the batch.
  - The 'quality_results' dict maps file_id -> QualityResult, enabling the
    conditional router to decide per-file pass/quarantine without re-reading.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Enums — typed, serializable
# ---------------------------------------------------------------------------


class ProcessingStatus(StrEnum):
    """Lifecycle status of a file through the pipeline."""

    PENDING = "pending"
    INGESTED = "ingested"
    VALIDATED = "validated"
    PROFILED = "profiled"
    CATALOGED = "cataloged"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class DataAssetType(StrEnum):
    TABLE = "table"
    VIEW = "view"
    FILE = "file"
    STREAM = "stream"
    TOPIC = "topic"
    MODEL = "model"


# ---------------------------------------------------------------------------
# Domain data models
# ---------------------------------------------------------------------------


@dataclass
class FileRecord:
    """
    Represents a single file discovered in the Bronze layer.

    This is the atomic unit of work in the pipeline — each file flows through
    ingestion -> validation -> profiling -> cataloging independently.
    """

    file_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str = ""
    file_name: str = ""
    file_size_bytes: int = 0
    file_format: str = ""  # json, csv, parquet, pdf, txt
    source_system: str = ""
    object_type: str = ""
    partition_date: str = ""  # YYYY-MM-DD from the Hive partition
    discovered_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: ProcessingStatus = ProcessingStatus.PENDING
    error_message: str = ""


@dataclass
class QualityResult:
    """
    Outcome of the Great Expectations validation for a single file.
    Stored in the state dict keyed by file_id so the conditional router
    can make per-file pass/quarantine decisions.
    """

    file_id: str = ""
    success: bool = False
    score: float = 0.0
    threshold: float = 0.95
    total_expectations: int = 0
    failed_expectations: int = 0
    validation_json: dict = field(default_factory=dict)
    quarantine_path: str = ""
    execution_secs: float = 0.0


@dataclass
class ProfileResult:
    """
    Statistical profile computed for a file's schema in the profiling node.
    Used downstream by the cataloging node to populate asset metadata.
    """

    file_id: str = ""
    schema_fields: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    column_count: int = 0
    null_rates: dict[str, float] = field(default_factory=dict)
    distinct_rates: dict[str, float] = field(default_factory=dict)
    inferred_types: dict[str, str] = field(default_factory=dict)
    sample_data: list[dict[str, Any]] = field(default_factory=list)
    profile_completed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class CatalogEntry:
    """
    The output of the cataloging node — a single entry written to the vector
    store (catalog.data_assets) with an embedding for semantic search.
    """

    file_id: str = ""
    asset_name: str = ""
    asset_type: DataAssetType = DataAssetType.FILE
    source_system: str = ""
    schema_name: str = ""
    table_name: str = ""
    file_path: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    quality_score: float = 0.0
    row_count: int = 0
    partition_count: int = 0
    embedding: list[float] | None = None  # 1536-dim from Titan Embeddings
    metadata_json: dict = field(default_factory=dict)
    cataloged_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# AgentState — the LangGraph state schema
# ---------------------------------------------------------------------------


class AgentState(TypedDict, total=False):
    """
    Strictly-typed state dictionary for the LangGraph StateGraph.

    Every key is optional (total=False) so that the graph can be initialized
    with minimal state and each node only writes the keys it owns.

    Fields:
      run_id:         Unique identifier for this graph execution.
      thread_id:      LangGraph thread identifier for checkpointing.
      files:          List of FileRecords discovered across Bronze.
      current_file_id: The file_id currently being processed (for single-file
                       routing accuracy in the conditional edge).
      quality_results: Map from file_id -> QualityResult.
      profile_results: Map from file_id -> ProfileResult.
      catalog_entries: List of successfully created CatalogEntry records.
      ingestion_summary:  Human-readable summary from the ingestion node.
      profiling_summary:  Human-readable summary from the profiling node.
      cataloging_summary: Human-readable summary from the cataloging node.
      error:           Global error message (set when a node catches a fatal).
      retry_count:     Number of retry attempts for the current file.
      errors:          List of error messages accumulated across nodes.
      start_time:      ISO timestamp of graph start.
      end_time:        ISO timestamp of graph completion.
    """

    run_id: str
    thread_id: str
    files: list[dict[str, Any]]
    current_file_id: str
    quality_results: dict[str, dict[str, Any]]
    profile_results: dict[str, dict[str, Any]]
    catalog_entries: list[dict[str, Any]]
    ingestion_summary: str
    profiling_summary: str
    cataloging_summary: str
    error: str
    retry_count: int
    errors: list[str]
    start_time: str
    end_time: str


# ---------------------------------------------------------------------------
# Helper: factory functions
# ---------------------------------------------------------------------------


def new_run_state(thread_id: str | None = None) -> AgentState:
    """Create a blank AgentState for a new graph execution."""
    return AgentState(
        run_id=str(uuid.uuid4()),
        thread_id=thread_id or str(uuid.uuid4()),
        files=[],
        current_file_id="",
        quality_results={},
        profile_results={},
        catalog_entries=[],
        ingestion_summary="",
        profiling_summary="",
        cataloging_summary="",
        error="",
        retry_count=0,
        errors=[],
        start_time=datetime.now(UTC).isoformat(),
        end_time="",
    )


def file_record_to_dict(fr: FileRecord) -> dict[str, Any]:
    return asdict(fr)


def quality_result_to_dict(qr: QualityResult) -> dict[str, Any]:
    return asdict(qr)


def profile_result_to_dict(pr: ProfileResult) -> dict[str, Any]:
    return asdict(pr)


def catalog_entry_to_dict(ce: CatalogEntry) -> dict[str, Any]:
    return asdict(ce)
