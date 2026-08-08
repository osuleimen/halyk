"""
FastAPI Application — Admin panel and API for the Covenant Compliance Agent.
Supports multi-provider LLM configuration.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import OUTPUT_PATH, SCENARIOS, TEMPLATE_PATH, load_providers, save_providers
from app.agent.graph import run_agent, set_log_callback
from app.services.evaluator import evaluate
from app.services.llm_factory import test_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Agent state ──
agent_status = {
    "state": "idle",
    "current_scenario": None,
    "progress": 0,
    "total": len(SCENARIOS),
    "started_at": None,
    "error": None,
}
agent_answers: dict = {}
agent_logs: list[str] = []
ws_clients: list[WebSocket] = []

# ── HITL State ──
hitl_event: asyncio.Event = None
hitl_state = {
    "pending": False,
    "transaction_id": None,
    "question": None,
    "decision": None
}

# ── Metrics State ──
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "history.json")
try:
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            run_history = json.load(f)
    else:
        run_history = []
except Exception:
    run_history = []

def save_history():
    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(run_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save history: {e}")


def broadcast_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    agent_logs.append(line)
    if len(agent_logs) > 500:
        agent_logs.pop(0)
    for ws in ws_clients[:]:
        try:
            asyncio.get_event_loop().call_soon_threadsafe(
                asyncio.ensure_future, ws.send_text(line)
            )
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🌐 Halyk AI Challenge — Agent Server started")
    # Load previous answers if exist
    from config import OUTPUT_PATH
    import json
    import os
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                agent_answers.update(data.get("answers", {}))
                logger.info(f"Loaded answers for {len(agent_answers)} scenarios from submission.json")
        except Exception as e:
            logger.error(f"Failed to load submission.json on startup: {e}")
            
    global hitl_event
    hitl_event = asyncio.Event()
    
    yield

app = FastAPI(title="Halyk AI Challenge — Covenant Agent", lifespan=lifespan)

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ═══ Pages ═══

@app.get("/", response_class=HTMLResponse)
async def admin_panel():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()
            return HTMLResponse(content, headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            })
    return HTMLResponse("<h1>Admin panel not found</h1>")


# ═══ Provider API ═══

class ProviderUpdate(BaseModel):
    provider_id: str
    api_key: str | None = None
    enabled: bool | None = None

class RunRequest(BaseModel):
    scenarios: list[str] | None = None


@app.get("/api/providers")
async def api_get_providers():
    providers = load_providers()
    # Mask API keys for security
    safe = {}
    for pid, cfg in providers.items():
        safe[pid] = dict(cfg)
        key = cfg.get("api_key", "")
        safe[pid]["api_key_set"] = bool(key)
        safe[pid]["api_key_masked"] = (key[:6] + "..." + key[-4:]) if len(key) > 10 else ("***" if key else "")
        del safe[pid]["api_key"]
    return safe


@app.post("/api/providers")
async def api_update_provider(req: ProviderUpdate):
    providers = load_providers()
    if req.provider_id not in providers:
        return JSONResponse({"error": f"Unknown provider: {req.provider_id}"}, status_code=404)

    if req.api_key is not None:
        providers[req.provider_id]["api_key"] = req.api_key
    if req.enabled is not None:
        providers[req.provider_id]["enabled"] = req.enabled

    save_providers(providers)
    return {"ok": True, "provider": req.provider_id}


@app.post("/api/providers/test")
async def api_test_provider(req: ProviderUpdate):
    providers = load_providers()
    if req.provider_id not in providers:
        return JSONResponse({"error": f"Unknown provider"}, status_code=404)

    cfg = dict(providers[req.provider_id])
    if req.api_key:
        cfg["api_key"] = req.api_key

    result = test_provider(req.provider_id, cfg)
    return result


# ═══ Agent API ═══

@app.post("/api/run")
async def api_run(req: RunRequest):
    global agent_status, agent_answers

    if agent_status["state"] == "running":
        return JSONResponse({"error": "Agent is already running"}, status_code=409)

    scenarios = req.scenarios or SCENARIOS

    agent_status = {
        "state": "running",
        "current_scenario": None,
        "progress": 0,
        "total": len(scenarios),
        "started_at": datetime.now().isoformat(),
        "error": None,
    }
    agent_logs.clear()

    def _run():
        global agent_status, agent_answers
        try:
            set_log_callback(broadcast_log)
            from app.agent.graph import run_agent_stream
            
            start_time = datetime.now()
            for state in run_agent_stream(scenarios):
                if state.get("current_scenario"):
                    agent_status["current_scenario"] = state["current_scenario"]
                
                pending = state.get("pending_scenarios", [])
                agent_status["progress"] = agent_status["total"] - len(pending)
                
                if state.get("answers"):
                    agent_answers.update(state["answers"])

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            # Record metrics
            try:
                from app.services.evaluator import evaluate
                eval_result = evaluate(agent_answers)
                
                # count correct
                correct = sum(
                    1 for s in eval_result.get("scenarios", {}).values() 
                    for c in s.get("covenants", {}).values() if c.get("is_correct")
                )
                total_covenants = sum(
                    len(s.get("covenants", {})) for s in eval_result.get("scenarios", {}).values()
                )
                
                providers = load_providers()
                active = next((p for p in providers.values() if p.get("enabled")), {})
                
                run_history.append({
                    "timestamp": end_time.isoformat(),
                    "model": active.get("models", {}).get("fast", "unknown"),
                    "scenarios": scenarios,
                    "duration_sec": duration,
                    "correct": correct,
                    "total": total_covenants,
                    "accuracy": f"{(correct/total_covenants)*100:.1f}%" if total_covenants > 0 else "0%"
                })
                save_history()
            except Exception as e:
                logger.error(f"Failed to calculate metrics: {e}")

            agent_status["state"] = "done"
            broadcast_log("🏁 Agent finished successfully!")
        except Exception as e:
            agent_status["state"] = "error"
            agent_status["error"] = str(e)
            broadcast_log(f"❌ Error: {e}")
            logger.exception("Agent error")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"status": "started", "scenarios": scenarios}


@app.get("/api/status")
async def api_status():
    return agent_status


@app.get("/api/answers")
async def api_answers():
    return agent_answers


@app.get("/api/logs")
async def api_logs():
    return {"logs": agent_logs[-100:]}


@app.get("/api/metrics")
async def api_metrics():
    return {"history": run_history}


@app.get("/api/evaluate")
async def api_evaluate():
    if not agent_answers:
        return {"error": "No answers yet"}
    return evaluate(agent_answers)


@app.get("/api/submission")
async def api_submission():
    if os.path.exists(OUTPUT_PATH):
        return FileResponse(OUTPUT_PATH, filename="submission.json", media_type="application/json")
    return JSONResponse({"error": "submission.json not found"}, status_code=404)


@app.get("/api/download_submission")
async def api_download_submission():
    import zipfile
    import io
    if not os.path.exists(OUTPUT_PATH):
        return JSONResponse({"error": "submission.json not found"}, status_code=404)
        
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(OUTPUT_PATH, "submission.json")
        if os.path.exists("covenant_logic_graph.md"):
            zipf.write("covenant_logic_graph.md", "covenant_logic_graph.md")
            
    zip_buffer.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        zip_buffer, 
        media_type="application/x-zip-compressed", 
        headers={"Content-Disposition": "attachment; filename=submission.zip"}
    )


class EditAnswerRequest(BaseModel):
    scenario_id: str
    covenant_id: str
    status: str
    actual: float
    reasoning: str

@app.post("/api/edit_answer")
async def api_edit_answer(req: EditAnswerRequest):
    if req.scenario_id not in agent_answers:
        agent_answers[req.scenario_id] = {}
        
    old_data = agent_answers.get(req.scenario_id, {}).get(req.covenant_id, {})
    agent_answers[req.scenario_id][req.covenant_id] = {
        "status": req.status,
        "actual": req.actual,
        "evidence_txn_id": old_data.get("evidence_txn_id"),
        "reasoning": req.reasoning,
        "graph_mermaid": old_data.get("graph_mermaid")
    }
    
    # Save to file immediately
    try:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump({"answers": agent_answers}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True}

@app.get("/api/transaction/{scenario_id}/{txn_id}")
async def api_transaction(scenario_id: str, txn_id: str):
    try:
        from app.services import ledger
        df = ledger._get_df()
        row = df[(df["scenario_id"] == scenario_id) & (df["txn_id"] == txn_id)]
        if row.empty:
            return JSONResponse({"error": "Transaction not found"}, status_code=404)
        r = row.iloc[0].to_dict()
        return {
            "txn_id": r["txn_id"],
            "date": r["date"],
            "amount": r["amount"],
            "currency": r["currency"],
            "counterparty": r["counterparty"],
            "description": r["description"]
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ═══ HITL API ═══

@app.post("/api/hitl/request")
async def api_hitl_request(req: dict):
    hitl_state["pending"] = True
    hitl_state["transaction_id"] = req.get("transaction_id")
    hitl_state["question"] = req.get("question")
    hitl_state["decision"] = None
    hitl_event.clear()
    
    broadcast_log(f"⚠️ Ожидание решения человека по транзакции {hitl_state['transaction_id']}")
    
    await hitl_event.wait()
    
    hitl_state["pending"] = False
    broadcast_log(f"✅ Человек ответил: {hitl_state['decision']}")
    return {"decision": hitl_state["decision"]}

@app.get("/api/hitl/pending")
async def api_hitl_pending():
    return hitl_state

@app.post("/api/hitl/resolve")
async def api_hitl_resolve(req: dict):
    hitl_state["decision"] = req.get("decision")
    hitl_event.set()
    return {"ok": True}


@app.get("/api/documents")
async def api_documents():
    from app.services.document_store import INDEX_CACHE_PATH
    if os.path.exists(INDEX_CACHE_PATH):
        with open(INDEX_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"error": "No document index yet"}


@app.post("/api/clear-cache")
async def api_clear_cache():
    """Clear extraction and classification caches."""
    from app.services.document_store import EXTRACT_CACHE_PATH, INDEX_CACHE_PATH
    for p in [EXTRACT_CACHE_PATH, INDEX_CACHE_PATH]:
        if os.path.exists(p):
            os.remove(p)
    return {"ok": True, "message": "Cache cleared"}


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    """Upload a new dataset zip file, extract it, and clear caches."""
    import zipfile
    import shutil
    from config import DATA_DIR
    from app.services.document_store import EXTRACT_CACHE_PATH, INDEX_CACHE_PATH

    zip_path = os.path.join(DATA_DIR, file.filename)
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # Save zip
    with open(zip_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
        
    # Extract zip
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(DATA_DIR)
        os.remove(zip_path)  # Cleanup zip
        
        # Clear caches
        for p in [EXTRACT_CACHE_PATH, INDEX_CACHE_PATH]:
            if os.path.exists(p):
                os.remove(p)
                
        return {"ok": True, "message": "Новый датасет успешно загружен. Кэш очищен."}
    except Exception as e:
        logger.exception("Upload failed")
        return JSONResponse({"error": f"Failed to extract zip: {e}"}, status_code=400)


# ═══ WebSocket ═══

@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    ws_clients.append(websocket)
    try:
        for line in agent_logs[-50:]:
            await websocket.send_text(line)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_clients.remove(websocket)
