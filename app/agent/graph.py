"""
LangGraph Agent — the main covenant compliance analysis graph.
Uses dynamic LLM provider from config (Gemini, DeepSeek, Muse Spark, etc.)
"""
from __future__ import annotations
import json
import logging
import os
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS, set_runtime_data, get_answers
from app.agent.prompts import ANALYST_SYSTEM_PROMPT
from app.services import document_store
from app.services.llm_factory import create_llm
from config import (
    get_active_provider, SCENARIO_TO_ACCOUNT,
    TEMPLATE_PATH, OUTPUT_PATH, TEAM_NAME, CONTACT_EMAIL,
)

logger = logging.getLogger(__name__)

# ── Logging callback (for WebSocket streaming) ──
_log_callback = None

def set_log_callback(cb):
    global _log_callback
    _log_callback = cb

def _log(msg: str):
    logger.info(msg)
    if _log_callback:
        _log_callback(msg)


# ═══════════════════════════════════════
# Node: index_documents
# ═══════════════════════════════════════

def index_documents(state: AgentState) -> dict:
    """Extract and classify all documents."""
    _log("📂 Indexing documents...")

    texts = document_store.extract_all(
        use_cache=True,
        progress_callback=lambda i, t, f, m: _log(f"  Extract [{i}/{t}] {f} ({m})")
    )
    _log(f"  ✅ Extracted {len(texts)} documents")

    index = document_store.build_index(
        texts, use_cache=True,
        progress_callback=lambda i, t, f, dt: _log(f"  Classify [{i}/{t}] {f} → {dt}")
    )
    _log(f"  ✅ Indexed {len(index)} documents")

    # Inject into tools
    set_runtime_data(texts, index, state.get("answers", {}))

    return {
        "document_index": index,
        "extracted_texts": texts,
        "logs": [f"Indexed {len(texts)} documents"],
    }


# ═══════════════════════════════════════
# Node: pick_scenario
# ═══════════════════════════════════════

def pick_scenario(state: AgentState) -> dict:
    """Pick the next scenario to analyze."""
    pending = state.get("pending_scenarios", [])
    if not pending:
        return {"current_scenario": None}

    scenario_id = pending[0]
    remaining = pending[1:]
    account_id = SCENARIO_TO_ACCOUNT.get(scenario_id, "?")

    _log(f"\n🎯 Analyzing scenario {scenario_id} ({account_id}) — {len(remaining)} remaining")

    prompt = ANALYST_SYSTEM_PROMPT.format(
        scenario_id=scenario_id,
        account_id=account_id,
    )

    return {
        "current_scenario": scenario_id,
        "pending_scenarios": remaining,
        "messages": [
            SystemMessage(content=prompt),
            HumanMessage(content=(
                f"Проанализируй ковенанты для заёмщика {scenario_id} (счёт {account_id}).\n"
                f"Тебе нужно определить status, actual и evidence_txn_id для ковенантов 6.1, 6.2 и 6.3.\n"
                f"В submit_answer обязательно заполняй поле 'reasoning' — напиши человекочитаемый комментарий с объяснением.\n"
                f"Начни с поиска кредитного договора через list_documents.\n"
                f"ВАЖНО: Ни в коем случае не пытайся складывать или вычитать большие суммы транзакций в уме! Всегда используй инструмент `query_ledger_sql` для вычисления агрегаций (SUM, AVG) по леджеру. Например, чтобы найти сумму операционных расходов (исключая определенные транзакции), напиши SQL запрос с нужными условиями."
            )),
        ],
    }


# ═══════════════════════════════════════
# Node: save_answer
# ═══════════════════════════════════════

def save_answer(state: AgentState) -> dict:
    """Save accumulated answers."""
    scenario_id = state.get("current_scenario")
    answers = get_answers()

    if scenario_id and scenario_id in answers:
        covs = answers[scenario_id]
        _log(f"💾 Saved answers for {scenario_id}:")
        for cid in sorted(covs.keys()):
            c = covs[cid]
            _log(f"   {cid}: {c['status']} | actual={c['actual']} | evidence={c.get('evidence_txn_id')}")
    else:
        _log(f"⚠️ No answers captured for {scenario_id}")

    return {"answers": answers, "messages": []}


# ═══════════════════════════════════════
# Routing
# ═══════════════════════════════════════

def has_more_scenarios(state: AgentState) -> Literal["pick_scenario", "compile"]:
    pending = state.get("pending_scenarios", [])
    return "pick_scenario" if pending else "compile"


# ═══════════════════════════════════════
# Node: compile
# ═══════════════════════════════════════

def compile_submission(state: AgentState) -> dict:
    """Compile final submission.json."""
    _log("\n📦 Compiling submission.json...")

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        submission = json.load(f)

    pid, pcfg = get_active_provider()
    submission["team"] = TEAM_NAME
    submission["contact_email"] = CONTACT_EMAIL
    submission["model"] = pcfg["models"]["pro"]

    answers = state.get("answers", {})
    for scenario_id in submission.get("answers", {}):
        if scenario_id in answers:
            for cov_id in submission["answers"][scenario_id]:
                if cov_id in answers[scenario_id]:
                    submission["answers"][scenario_id][cov_id] = answers[scenario_id][cov_id]

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(submission, f, ensure_ascii=False, indent=2)

    filled = sum(
        1 for s in submission["answers"].values()
        for c in s.values() if c.get("status") is not None
    )
    total = sum(len(s) for s in submission["answers"].values())
    _log(f"✅ submission.json saved ({filled}/{total} cells)")

    return {"logs": [f"Submission compiled: {filled}/{total} cells"]}


# ═══════════════════════════════════════
# Build the graph
# ═══════════════════════════════════════

def build_graph(scenarios: list[str] | None = None) -> StateGraph:
    """Build the full LangGraph agent graph with dynamic provider."""
    from config import SCENARIOS
    if scenarios is None:
        scenarios = SCENARIOS

    # Create LLM from active provider
    pid, pcfg = get_active_provider()
    _log(f"🤖 Using provider: {pcfg['name']} ({pcfg['models']['pro']})")

    llm = create_llm(pid, pcfg, tier="pro")

    # Create ReAct agent
    analyst = create_react_agent(model=llm, tools=ALL_TOOLS)

    graph = StateGraph(AgentState)
    graph.add_node("index_documents", index_documents)
    graph.add_node("pick_scenario", pick_scenario)
    graph.add_node("analyst", analyst)
    graph.add_node("save_answer", save_answer)
    graph.add_node("compile", compile_submission)

    graph.add_edge(START, "index_documents")
    graph.add_edge("index_documents", "pick_scenario")
    graph.add_edge("pick_scenario", "analyst")
    graph.add_edge("analyst", "save_answer")
    graph.add_conditional_edges("save_answer", has_more_scenarios)
    graph.add_edge("compile", END)

    return graph


def run_agent_stream(scenarios: list[str] | None = None):
    """Run the full agent pipeline and yield state updates."""
    from config import SCENARIOS
    if scenarios is None:
        scenarios = SCENARIOS

    _log("🚀 Starting Covenant Compliance Agent")
    _log(f"   Scenarios: {scenarios}")

    graph = build_graph(scenarios)
    app = graph.compile()

    initial_state = {
        "document_index": {},
        "extracted_texts": {},
        "pending_scenarios": list(scenarios),
        "current_scenario": None,
        "answers": {},
        "messages": [],
        "logs": [],
    }

    for state in app.stream(initial_state, stream_mode="values", config={"recursion_limit": 150}):
        yield state

    _log("\n🏁 Agent finished!")

def run_agent(scenarios: list[str] | None = None) -> dict:
    """Run the full agent pipeline synchronously (legacy)."""
    final_state = None
    for state in run_agent_stream(scenarios):
        final_state = state
    return final_state.get("answers", {}) if final_state else {}
