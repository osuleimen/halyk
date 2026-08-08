"""
Agent State — TypedDict for the LangGraph covenant analysis agent.
"""
from __future__ import annotations
from typing import Annotated, Any
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """State flowing through the LangGraph covenant analyst graph."""

    # ── Persistent (set once during indexing) ──
    document_index: dict[str, Any]       # {filename: classification_meta}
    extracted_texts: dict[str, str]       # {filename: full text}  (kept in memory)

    # ── Scenario queue ──
    pending_scenarios: list[str]          # scenarios still to process
    current_scenario: str | None          # scenario being analysed right now

    # ── Accumulated answers ──
    answers: dict[str, dict[str, Any]]    # {scenario_id: {cov_id: {status, actual, evidence_txn_id}}}

    # ── Agent conversation (LangGraph messages) ──
    messages: Annotated[list, add_messages]

    # ── Logging / UI ──
    logs: list[str]                       # human-readable log lines
