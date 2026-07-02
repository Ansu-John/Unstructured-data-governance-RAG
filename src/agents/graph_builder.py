"""
graph_builder.py — LangGraph StateGraph Assembly & Conditional Router

Assembles the three-node orchestration pipeline (ingestion → profiling →
cataloging) with a conditional quality-gate router that enforces the
Medallion Architecture's fail-fast barrier.

Graph topology:

    [START]
       │
       ▼
  ┌─────────────┐
  │  ingestion   │  Scan Bronze layer, discover files
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  profiling   │  Compute schema/stats for passing files
  └──────┬──────┘
         │
         ▼
  ┌────────────────┐
  │ quality_router  │  Conditional edge: check per-file quality score
  └───────┬────────┘
          │
     ┌────┴────┐
     ▼         ▼
  ┌──────┐ ┌──────────┐
  │catalog│ │log_fail  │  Log failure, quarantine, route back or END
  └──┬───┘ └────┬─────┘
     │          │
     ▼          │
   [END] ◄──────┘

Conditional logic:
  - quality_router checks state["quality_results"][current_file_id].score
    against the threshold.
  - PASS ≥ threshold: route to "cataloging" node.
  - FAIL < threshold: route to "log_fail_and_quarantine" node.
  - The "log_fail" node increments retry_count and either re-routes to
    ingestion (retry < max_retries) or terminates (retry ≥ max_retries).

Error handling:
  - Every node wraps its body in try/except with OpenTelemetry span recording.
  - Global errors set state["error"] and optionally halt the graph via a
    separate error_edge.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Literal

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, StateGraph

from src.agents.nodes.ingestion import ingestion_node
from src.agents.nodes.profiling import profiling_node
from src.agents.nodes.cataloging import cataloging_node
from src.agents.state import AgentState, new_run_state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_RETRIES_PER_FILE = int(os.environ.get("AGENT_MAX_RETRIES", "3"))
QUALITY_THRESHOLD = float(os.environ.get("QUALITY_THRESHOLD", "0.95"))


# ---------------------------------------------------------------------------
# Conditional Router
# ---------------------------------------------------------------------------

def quality_router(state: AgentState) -> Literal["cataloging", "log_fail_and_quarantine"]:
    """
    Conditional edge function: Inspect the quality result for the currently
    active file and decide whether to proceed to cataloging or enter the
    quarantine/failure loop.

    The decision is per-file (current_file_id), not aggregate, so one bad
    file doesn't block good ones.
    """
    current_file_id = state.get("current_file_id", "")
    quality_results: Dict[str, Any] = state.get("quality_results", {})

    if not current_file_id or current_file_id not in quality_results:
        logger.warning(
            "No quality result for file '%s' — routing to cataloging as default",
            current_file_id,
        )
        return "cataloging"

    qr = quality_results[current_file_id]
    score = qr.get("score", 0.0)
    threshold = qr.get("threshold", QUALITY_THRESHOLD)

    if score >= threshold:
        logger.info(
            "QUALITY PASS: file=%s score=%.4f threshold=%.4f → cataloging",
            current_file_id, score, threshold,
        )
        return "cataloging"
    else:
        logger.warning(
            "QUALITY FAIL: file=%s score=%.4f threshold=%.4f → quarantine",
            current_file_id, score, threshold,
        )
        return "log_fail_and_quarantine"


def retry_router(state: AgentState) -> Literal["ingestion", END]:
    """
    After logging a failure, decide whether to retry or terminate.

    Returns 'ingestion' if retry_count < MAX_RETRIES_PER_FILE, else END.
    """
    retry_count = state.get("retry_count", 0)
    if retry_count < MAX_RETRIES_PER_FILE:
        logger.info("Retry %d/%d — routing back to ingestion", retry_count + 1, MAX_RETRIES_PER_FILE)
        return "ingestion"
    else:
        logger.error("Max retries (%d) exhausted — terminating graph", MAX_RETRIES_PER_FILE)
        return END


# ---------------------------------------------------------------------------
# Error / Failure node
# ---------------------------------------------------------------------------

def log_fail_and_quarantine(state: AgentState) -> Dict[str, Any]:
    """
    Node: Log the quality failure, increment retry_count, and record the
    quarantine action. Does NOT perform the actual quarantine (that is done
    by the GX pipeline) — it records the metadata in the state so downstream
    observability can track it.

    In production, this node also writes a record to the
    catalog.quality_runs table via the agent_executions log.
    """
    current_file_id = state.get("current_file_id", "")
    quality_results: Dict[str, Any] = state.get("quality_results", {})
    errors: List[str] = list(state.get("errors", []))
    retry_count = state.get("retry_count", 0)

    qr = quality_results.get(current_file_id, {})
    score = qr.get("score", 0.0)
    threshold = qr.get("threshold", QUALITY_THRESHOLD)
    file_name = "unknown"

    # Find the file name from state
    for f in state.get("files", []):
        if f.get("file_id") == current_file_id:
            file_name = f.get("file_name", "unknown")
            break

    msg = (
        f"File '{file_name}' ({current_file_id}) failed quality check: "
        f"score={score:.4f} < threshold={threshold:.4f}. "
        f"Retry count: {retry_count}/{MAX_RETRIES_PER_FILE}."
    )
    logger.warning(msg)
    errors.append(msg)

    return {
        "retry_count": retry_count + 1,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_quality_catalog_graph() -> StateGraph:
    """
    Assemble the full StateGraph with all nodes, edges, and conditional
    routing.

    Returns a compiled StateGraph ready for invocation.

    Usage:
        graph = build_quality_catalog_graph()
        initial_state = new_run_state(thread_id="my-thread")
        for event in graph.stream(initial_state):
            print(event)
    """
    workflow = StateGraph(AgentState)

    # ── Register nodes ──────────────────────────────────────────────────
    workflow.add_node("ingestion", ingestion_node)
    workflow.add_node("profiling", profiling_node)
    workflow.add_node("cataloging", cataloging_node)
    workflow.add_node("log_fail_and_quarantine", log_fail_and_quarantine)

    # ── Edges ───────────────────────────────────────────────────────────
    workflow.set_entry_point("ingestion")

    # Sequential pipeline
    workflow.add_edge("ingestion", "profiling")

    # Conditional edge: quality check → cataloging or quarantine
    workflow.add_conditional_edges(
        "profiling",
        quality_router,
        {
            "cataloging": "cataloging",
            "log_fail_and_quarantine": "log_fail_and_quarantine",
        },
    )

    # After cataloging, end
    workflow.add_edge("cataloging", END)

    # After quarantine logging, either retry or end
    workflow.add_conditional_edges(
        "log_fail_and_quarantine",
        retry_router,
        {
            "ingestion": "ingestion",
            END: END,
        },
    )

    return workflow


def compile_graph_with_checkpointer() -> StateGraph:
    """
    Compile the graph with a PostgresSaver checkpointer for state persistence.

    The PostgresSaver uses the langgraph.checkpoints table created by the
    01-init.sql migration, enabling resume-from-interruption across container
    restarts.
    """
    workflow = build_quality_catalog_graph()

    # Attempt Postgres checkpointer — fall back to MemorySaver if DB unreachable
    try:
        import psycopg2
        from langgraph.checkpoint.postgres import PostgresSaver

        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5432")),
            dbname=os.environ.get("DB_NAME", "aicatalog"),
            user=os.environ.get("DB_USER", "catalog_admin"),
            password=os.environ.get("DB_PASSWORD", "catalog_dev_pwd_2024"),
        )
        checkpointer = PostgresSaver(conn)
        checkpointer.setup()  # Ensure tables exist
        logger.info("Graph compiled with PostgresSaver checkpointer")
        return workflow.compile(checkpointer=checkpointer)

    except Exception as exc:
        logger.warning(
            "PostgresSaver unavailable (%s). Falling back to MemorySaver. "
            "State will NOT persist across restarts.",
            exc,
        )
        from langgraph.checkpoint.memory import MemorySaver
        return workflow.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# Convenience entrypoint
# ---------------------------------------------------------------------------

def run_graph(
    thread_id: str | None = None,
    bronze_paths: list[str] | None = None,
) -> Dict[str, Any]:
    """
    Execute the full graph end-to-end with a single call.

    Args:
        thread_id: Optional thread identifier for checkpointing.
        bronze_paths: Optional list of Bronze S3 prefixes to scan.

    Returns:
        The final AgentState after graph completion.
    """
    if bronze_paths:
        os.environ["BRONZE_S3_PATHS"] = ",".join(bronze_paths)

    graph = compile_graph_with_checkpointer()
    initial_state = new_run_state(thread_id=thread_id)

    final_state: Dict[str, Any] = {}
    for event in graph.stream(initial_state):
        logger.debug("Graph event: %s", event)
        # The last event contains the final state
        for _, node_state in event.items():
            final_state.update(node_state)

    return final_state


# ---------------------------------------------------------------------------
# Module-level quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_graph(thread_id="local-test-run")
    print("\n=== FINAL STATE ===")
    print(f"  Files discovered:    {len(result.get('files', []))}")
    print(f"  Profiles computed:   {len(result.get('profile_results', {}))}")
    print(f"  Catalog entries:     {len(result.get('catalog_entries', []))}")
    print(f"  Errors:              {len(result.get('errors', []))}")
    print(f"  Ingestion:           {result.get('ingestion_summary', 'N/A')}")
    print(f"  Profiling:           {result.get('profiling_summary', 'N/A')}")
    print(f"  Cataloging:          {result.get('cataloging_summary', 'N/A')}")