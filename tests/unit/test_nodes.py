"""
test_nodes.py — Unit Tests for Standalone Node Functions

Tests the pure logic of each node function, state transitions, and data
model transformations in isolation from the LangGraph runtime.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.agents.state import (
    AgentState,
    FileRecord,
    QualityResult,
    ProfileResult,
    CatalogEntry,
    ProcessingStatus,
    DataAssetType,
    new_run_state,
    file_record_to_dict,
    quality_result_to_dict,
    profile_result_to_dict,
    catalog_entry_to_dict,
)


# ---------------------------------------------------------------------------
# Tests: FileRecord Data Model
# ---------------------------------------------------------------------------

class TestFileRecord:
    """Verify FileRecord creation, defaults, and serialization."""

    def test_create_with_defaults(self):
        """FileRecord generates a UUID file_id and sets PENDING status."""
        fr = FileRecord(file_path="s3://bucket/test.json", file_name="test.json")
        assert fr.file_id is not None
        assert len(fr.file_id) > 0
        assert fr.status == ProcessingStatus.PENDING
        assert fr.file_size_bytes == 0
        assert fr.error_message == ""

    def test_create_with_all_fields(self):
        """FileRecord with all fields explicitly set."""
        fr = FileRecord(
            file_id="custom-id",
            file_path="s3://bucket/crm/users/data.parquet",
            file_name="data.parquet",
            file_size_bytes=65536,
            file_format="parquet",
            source_system="crm",
            object_type="users",
            partition_date="2024-03-10",
            status=ProcessingStatus.INGESTED,
        )
        assert fr.file_id == "custom-id"
        assert fr.file_format == "parquet"
        assert fr.status == ProcessingStatus.INGESTED

    def test_serialization_roundtrip(self):
        """FileRecord serializes to dict and back."""
        fr = FileRecord(
            file_path="s3://bucket/test.json",
            file_name="test.json",
            source_system="crm",
        )
        d = file_record_to_dict(fr)
        assert d["file_path"] == "s3://bucket/test.json"
        assert d["source_system"] == "crm"
        assert d["status"] == ProcessingStatus.PENDING.value

    def test_status_enum_values(self):
        """Verify all enum values are valid."""
        assert ProcessingStatus.PENDING.value == "pending"
        assert ProcessingStatus.INGESTED.value == "ingested"
        assert ProcessingStatus.VALIDATED.value == "validated"
        assert ProcessingStatus.PROFILED.value == "profiled"
        assert ProcessingStatus.CATALOGED.value == "cataloged"
        assert ProcessingStatus.QUARANTINED.value == "quarantined"
        assert ProcessingStatus.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# Tests: QualityResult Data Model
# ---------------------------------------------------------------------------

class TestQualityResult:
    """Verify quality result creation and threshold logic."""

    def test_passing_result(self):
        """A passing quality result has score >= threshold."""
        qr = QualityResult(
            success=True,
            score=0.98,
            threshold=0.95,
            total_expectations=10,
            failed_expectations=0,
        )
        assert qr.success is True
        assert qr.score >= qr.threshold

    def test_failing_result(self):
        """A failing quality result has score < threshold."""
        qr = QualityResult(
            success=False,
            score=0.45,
            threshold=0.95,
            total_expectations=10,
            failed_expectations=5,
        )
        assert qr.success is False
        assert qr.score < qr.threshold

    def test_edge_case_exact_threshold(self):
        """Score exactly at threshold should be considered passing."""
        qr = QualityResult(
            success=True,
            score=0.95,
            threshold=0.95,
        )
        assert qr.score >= qr.threshold

    def test_serialization(self):
        """QualityResult serializes to dict with all fields."""
        qr = QualityResult(
            file_id="file-001",
            success=True,
            score=0.99,
            validation_json={"suite": "crm_users_suite"},
        )
        d = quality_result_to_dict(qr)
        assert d["file_id"] == "file-001"
        assert d["success"] is True
        assert d["validation_json"]["suite"] == "crm_users_suite"


# ---------------------------------------------------------------------------
# Tests: ProfileResult Data Model
# ---------------------------------------------------------------------------

class TestProfileResult:
    """Verify profile result data model."""

    def test_minimal_profile(self):
        """ProfileResult with minimal fields."""
        pr = ProfileResult(file_id="file-001")
        assert pr.file_id == "file-001"
        assert pr.row_count == 0
        assert pr.column_count == 0
        assert pr.schema_fields == []

    def test_full_profile(self):
        """ProfileResult with computed statistics."""
        pr = ProfileResult(
            file_id="file-001",
            schema_fields=[
                {"name": "id", "type": "LongType", "nullable": False},
                {"name": "email", "type": "StringType", "nullable": False},
            ],
            row_count=10000,
            column_count=2,
            null_rates={"id": 0.0, "email": 0.05},
            distinct_rates={"id": 1.0, "email": 0.75},
            inferred_types={"id": "LongType", "email": "StringType"},
        )
        assert pr.row_count == 10000
        assert pr.column_count == 2
        assert pr.null_rates["email"] == 0.05

    def test_serialization(self):
        """ProfileResult serializes to dict."""
        pr = ProfileResult(
            file_id="file-001",
            row_count=500,
            null_rates={"col_a": 0.0},
        )
        d = profile_result_to_dict(pr)
        assert d["row_count"] == 500
        assert d["null_rates"]["col_a"] == 0.0


# ---------------------------------------------------------------------------
# Tests: CatalogEntry Data Model
# ---------------------------------------------------------------------------

class TestCatalogEntry:
    """Verify catalog entry creation and serialization."""

    def test_minimal_entry(self):
        """CatalogEntry with minimal fields."""
        ce = CatalogEntry(
            file_id="file-001",
            asset_name="test_asset",
            source_system="crm",
        )
        assert ce.asset_name == "test_asset"
        assert ce.asset_type == DataAssetType.FILE
        assert ce.quality_score == 0.0

    def test_with_embedding(self):
        """CatalogEntry with a 1536-dimension embedding vector."""
        embedding = [0.1] * 1536
        ce = CatalogEntry(
            file_id="file-001",
            asset_name="users",
            source_system="crm",
            embedding=embedding,
        )
        assert len(ce.embedding) == 1536

    def test_asset_type_mapping(self):
        """Verify asset type enum values."""
        assert DataAssetType.TABLE.value == "table"
        assert DataAssetType.FILE.value == "file"
        assert DataAssetType.STREAM.value == "stream"

    def test_serialization(self):
        """CatalogEntry serializes to dict."""
        ce = CatalogEntry(
            file_id="file-001",
            asset_name="crm_users",
            asset_type=DataAssetType.TABLE,
            source_system="crm",
            description="Test description",
            tags=["crm", "parquet"],
            quality_score=0.95,
            row_count=15000,
        )
        d = catalog_entry_to_dict(ce)
        assert d["asset_name"] == "crm_users"
        assert d["asset_type"].value == "table"  # Enum value preserved
        assert d["quality_score"] == 0.95
        assert len(d["tags"]) == 2


# ---------------------------------------------------------------------------
# Tests: AgentState Initialization
# ---------------------------------------------------------------------------

class TestAgentState:
    """Verify AgentState creation and key structure."""

    def test_new_run_state_has_all_keys(self):
        """new_run_state() creates a state with all expected keys."""
        state = new_run_state(thread_id="test-thread-1")
        assert state["run_id"] is not None
        assert state["thread_id"] == "test-thread-1"
        assert state["files"] == []
        assert state["quality_results"] == {}
        assert state["profile_results"] == {}
        assert state["catalog_entries"] == []
        assert state["error"] == ""
        assert state["retry_count"] == 0
        assert state["errors"] == []
        assert state["start_time"] is not None

    def test_thread_id_generation(self):
        """A thread_id is generated if not provided."""
        state = new_run_state()
        assert state["thread_id"] is not None
        assert len(state["thread_id"]) > 0

    def test_state_is_mutable_dict(self):
        """AgentState is a TypedDict — behaves as a mutable dict."""
        state = new_run_state()
        state["files"] = [{"file_id": "test", "file_path": "s3://test"}]
        state["error"] = "some error"
        assert len(state["files"]) == 1
        assert state["error"] == "some error"

    def test_json_serializable(self):
        """AgentState must be JSON-serializable for checkpointer."""
        state = new_run_state()
        state["files"] = [{"file_id": "f1", "file_path": "s3://test"}]
        state["quality_results"] = {"f1": {"success": True, "score": 0.95}}
        state["errors"] = ["warning: something minor"]
        dumped = json.dumps(dict(state))
        loaded = json.loads(dumped)
        assert loaded["thread_id"] == state["thread_id"]
        assert loaded["quality_results"]["f1"]["score"] == 0.95


# ---------------------------------------------------------------------------
# Tests: Processing Status Transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    """Verify valid and invalid processing status transitions."""

    def test_valid_status_sequence(self):
        """The expected status sequence is valid."""
        statuses = [
            ProcessingStatus.PENDING,
            ProcessingStatus.INGESTED,
            ProcessingStatus.VALIDATED,
            ProcessingStatus.PROFILED,
            ProcessingStatus.CATALOGED,
        ]
        for s in statuses:
            assert isinstance(s, ProcessingStatus)

    def test_failure_any_state(self):
        """FAILED is always a valid status."""
        assert ProcessingStatus.FAILED in ProcessingStatus

    def test_quarantine_state(self):
        """QUARANTINED is a valid terminal state."""
        assert ProcessingStatus.QUARANTINED.value == "quarantined"


# ---------------------------------------------------------------------------
# Tests: Data Model Helpers
# ---------------------------------------------------------------------------

class TestDataModelHelpers:
    """Verify helper functions on data models."""

    def test_file_record_empty_fields(self):
        """FileRecord handles empty strings gracefully."""
        fr = FileRecord()
        assert fr.file_path == ""
        assert fr.file_name == ""
        assert fr.file_format == ""

    def test_quality_result_defaults(self):
        """QualityResult defaults are sensible."""
        qr = QualityResult()
        assert qr.score == 0.0
        assert qr.threshold == 0.95
        assert qr.total_expectations == 0
        assert qr.success is False

    def test_profile_result_defaults(self):
        """ProfileResult defaults are sensible."""
        pr = ProfileResult()
        assert pr.row_count == 0
        assert pr.column_count == 0
        assert pr.schema_fields == []
        assert pr.sample_data == []

    def test_catalog_entry_defaults(self):
        """CatalogEntry defaults are sensible."""
        ce = CatalogEntry()
        assert ce.quality_score == 0.0
        assert ce.row_count == 0
        assert ce.tags == []
        assert ce.embedding is None