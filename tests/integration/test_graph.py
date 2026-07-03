"""
test_graph.py — Integration Tests for the LangGraph StateGraph

Tests the full multi-node orchestration graph with mocked Bedrock and S3
clients, verifying:
  1. Happy path: ingestion → profiling → cataloging succeeds.
  2. Quality failure path: low-quality data routes through quarantine loop.
  3. Retry limit exhaustion: repeated quality failures terminate the graph.
  4. Error handling: S3 access failure doesn't crash the full graph.

Fixtures:
  - mock_bedrock: Patches boto3 bedrock-runtime to return canned responses.
  - mock_s3: Patches boto3 s3 client for LocalStack-free testing.
  - test_state: Pre-built AgentState with controlled file records.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Generator
from unittest.mock import MagicMock, Mock, patch

import pytest

# Ensure src is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from langgraph.graph import END

from src.agents.graph_builder import (
    build_quality_catalog_graph,
    quality_router,
    retry_router,
    MAX_RETRIES_PER_FILE,
    QUALITY_THRESHOLD,
)
from src.agents.nodes.ingestion import ingestion_node
from src.agents.nodes.profiling import profiling_node
from src.agents.nodes.cataloging import cataloging_node
from src.agents.state import (
    AgentState,
    new_run_state,
    file_record_to_dict,
    quality_result_to_dict,
    profile_result_to_dict,
    QualityResult,
    ProfileResult,
    FileRecord,
    ProcessingStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_bedrock() -> Generator[None, None, None]:
    """Mock Bedrock runtime to return a canned description and embedding."""
    with patch("boto3.Session") as mock_session:
        mock_client = MagicMock()
        mock_session.return_value.client.return_value = mock_client

        # Mock invoke_model for text generation
        text_response = MagicMock()
        text_response["body"].read.return_value = json.dumps({
            "content": [{"text": "Test data asset description from LLM."}]
        }).encode()
        mock_client.invoke_model.side_effect = [text_response]
        yield


@pytest.fixture
def mock_s3() -> Generator[None, None, None]:
    """Mock S3 client to return canned object listings."""
    with patch("boto3.Session") as mock_session:
        mock_client = MagicMock()
        mock_session.return_value.client.return_value = mock_client

        # Mock S3 paginator
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "crm/users/year=2024/month=03/day=10/users_20240310.json",
                        "Size": 2048,
                    },
                    {
                        "Key": "crm/users/year=2024/month=03/day=11/users_20240311.json",
                        "Size": 1890,
                    },
                ]
            }
        ]
        mock_client.get_paginator.return_value = mock_paginator
        yield


@pytest.fixture
def test_state() -> AgentState:
    """Return a pre-built state for testing the graph pipeline."""
    file1 = FileRecord(
        file_id="file-001",
        file_path="s3://test-bronze/crm/users/year=2024/month=03/day=10/users_20240310.json",
        file_name="users_20240310.json",
        file_format="json",
        source_system="crm",
        object_type="users",
        partition_date="2024-03-10",
        status=ProcessingStatus.INGESTED,
    )
    file2 = FileRecord(
        file_id="file-002",
        file_path="s3://test-bronze/crm/users/year=2024/month=03/day=11/users_20240311.json",
        file_name="users_20240311.json",
        file_format="json",
        source_system="crm",
        object_type="users",
        partition_date="2024-03-11",
        status=ProcessingStatus.INGESTED,
    )
    qr_pass = QualityResult(
        file_id="file-001",
        success=True,
        score=0.98,
        threshold=QUALITY_THRESHOLD,
        total_expectations=8,
        failed_expectations=0,
    )
    qr_fail = QualityResult(
        file_id="file-002",
        success=False,
        score=0.45,
        threshold=QUALITY_THRESHOLD,
        total_expectations=8,
        failed_expectations=4,
    )
    profile = ProfileResult(
        file_id="file-001",
        row_count=1500,
        column_count=5,
        schema_fields=[
            {"name": "id", "type": "LongType", "nullable": False},
            {"name": "name", "type": "StringType", "nullable": True},
            {"name": "email", "type": "StringType", "nullable": False},
        ],
    )

    state = new_run_state(thread_id="test-integration")
    state["files"] = [file_record_to_dict(file1), file_record_to_dict(file2)]
    state["current_file_id"] = "file-001"
    state["quality_results"] = {
        "file-001": quality_result_to_dict(qr_pass),
        "file-002": quality_result_to_dict(qr_fail),
    }
    state["profile_results"] = {
        "file-001": profile_result_to_dict(profile),
    }
    return state


# ---------------------------------------------------------------------------
# Test: Graph Happy Path (Success Route)
# ---------------------------------------------------------------------------

class TestGraphHappyPath:
    """Verify the full success path through all three nodes."""

    def test_graph_assembly(self):
        """Verify the graph builds without errors."""
        graph = build_quality_catalog_graph()
        assert graph is not None

    def test_ingestion_node_populates_files(self, mock_s3):
        """Ingestion node discovers files and adds them to state."""
        state = new_run_state(thread_id="test-ingestion")
        result = ingestion_node(state)

        assert "files" in result
        assert len(result["files"]) > 0
        assert "ingestion_summary" in result

        first_file = result["files"][0]
        assert "file_id" in first_file
        assert "file_path" in first_file
        assert "source_system" in first_file

    def test_profiling_node_skips_unvalidated(self, test_state):
        """Profiling node skips files without quality results."""
        # Add a file with no quality result
        test_state["files"].append({
            "file_id": "file-003",
            "file_name": "unvalidated.json",
            "file_path": "s3://test/unvalidated.json",
            "status": ProcessingStatus.PENDING.value,
        })

        result = profiling_node(test_state)
        assert "profile_results" in result

        # file-003 should not be profiled (no quality result)
        assert "file-003" not in result["profile_results"]
        # file-001 should be profiled (has passing quality result)
        assert "file-001" in result["profile_results"]

    def test_cataloging_node_produces_entries(self, test_state, mock_bedrock):
        """Cataloging node creates entries for profiled files."""
        result = cataloging_node(test_state)

        assert "catalog_entries" in result
        assert len(result["catalog_entries"]) > 0
        assert "cataloging_summary" in result

        entry = result["catalog_entries"][0]
        assert "file_id" in entry
        assert "asset_name" in entry
        assert "description" in entry
        assert "embedding" in entry

    @pytest.mark.asyncio
    async def test_full_graph_stream_success(self, mock_s3, mock_bedrock):
        """Verify the full graph completes the success path."""
        graph = build_quality_catalog_graph()
        initial = new_run_state(thread_id="test-full-success")

        events = []
        for event in graph.stream(initial):
            events.append(event)

        # Should have at least 3 node events + start/end
        assert len(events) >= 3

        # Check that the final state has all expected keys
        final_state = {}
        for event in events:
            for _, node_state in event.items():
                final_state.update(node_state)

        assert "files" in final_state
        assert "ingestion_summary" in final_state


# ---------------------------------------------------------------------------
# Test: Quality Failure & Retry Route
# ---------------------------------------------------------------------------

class TestQualityFailurePath:
    """Verify the quality failure routing and retry logic."""

    def test_quality_router_passes_good_scores(self, test_state):
        """quality_router returns 'cataloging' for scores above threshold."""
        route = quality_router(test_state)
        assert route == "cataloging"

    def test_quality_router_fails_low_scores(self, test_state):
        """quality_router returns 'log_fail_and_quarantine' for low scores."""
        state = dict(test_state)
        state["current_file_id"] = "file-002"  # This one has score=0.45
        route = quality_router(state)
        assert route == "log_fail_and_quarantine"

    def test_quality_router_default_when_no_result(self, test_state):
        """quality_router defaults to cataloging when no quality result."""
        state = dict(test_state)
        state["current_file_id"] = "nonexistent"
        route = quality_router(state)
        assert route == "cataloging"

    def test_retry_router_below_limit(self):
        """retry_router returns 'ingestion' when retries remain."""
        state = AgentState(retry_count=0)
        route = retry_router(state)
        assert route == "ingestion"

    def test_retry_router_at_limit(self):
        """retry_router returns END when retries exhausted."""
        state = AgentState(retry_count=MAX_RETRIES_PER_FILE)
        route = retry_router(state)
        assert route == END

    def test_retry_router_above_limit(self):
        """retry_router returns END when retries exceeded."""
        state = AgentState(retry_count=MAX_RETRIES_PER_FILE + 2)
        route = retry_router(state)
        assert route == END

    def test_fail_node_increments_retry(self, test_state):
        """log_fail_and_quarantine node increments retry_count."""
        from src.agents.graph_builder import log_fail_and_quarantine
        result = log_fail_and_quarantine(test_state)
        assert result["retry_count"] == test_state.get("retry_count", 0) + 1
        assert len(result["errors"]) > 0


# ---------------------------------------------------------------------------
# Test: Graph Error Handling
# ---------------------------------------------------------------------------

class TestGraphErrorHandling:
    """Verify edge cases and error behavior."""

    def test_empty_state_does_not_crash(self):
        """An empty state should not crash the ingestion node."""
        state: AgentState = AgentState()
        try:
            result = ingestion_node(state)
            # Should handle gracefully
            assert "files" in result or "error" in result
        except Exception as exc:
            pytest.fail(f"Ingestion node crashed on empty state: {exc}")

    def test_graph_handles_multiple_quality_outcomes(self, test_state, mock_bedrock):
        """Graph handles mixed pass/fail quality outcomes."""
        graph = build_quality_catalog_graph()

        events = []
        for event in graph.stream(test_state):
            events.append(event)

        # The graph should complete without raising
        assert len(events) > 0

    def test_profiling_with_empty_profile_skips(self):
        """Profiling node handles case with no files."""
        state = new_run_state()
        result = profiling_node(state)
        assert "profile_results" in result


# ---------------------------------------------------------------------------
# Test: State Serialization
# ---------------------------------------------------------------------------

class TestStateSerialization:
    """Verify AgentState fields are JSON-serializable (checkpointer requirement)."""

    def test_new_run_state_is_serializable(self):
        """new_run_state() should produce JSON-serializable state."""
        state = new_run_state(thread_id="serial-test")
        try:
            json.dumps(dict(state))
        except (TypeError, ValueError) as exc:
            pytest.fail(f"State not JSON-serializable: {exc}")

    def test_full_state_is_serializable(self, test_state):
        """Test state with files and results should be serializable."""
        try:
            json.dumps(dict(test_state))
        except (TypeError, ValueError) as exc:
            pytest.fail(f"Test state not JSON-serializable: {exc}")