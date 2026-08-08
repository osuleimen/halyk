"""
Celery tasks — тяжёлый прогон агента вынесен из threading в воркеры.
Используется только если is_celery_available() == True, иначе api_run фоллбэчит на Thread.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from app.celery_app import celery_app

    if celery_app is not None:

        @celery_app.task(bind=True, name="app.tasks.run_covenant_agent")
        def run_covenant_agent(self, scenarios: list[str], initiator: str = "Celery"):
            """Запускает полный пайплайн ReAct + Vision + verification в воркере."""
            from app.agent.graph import run_agent_stream, set_log_callback
            from app.services.evaluator import evaluate
            from config import load_providers, CACHE_DIR
            import json as _json
            import os as _os

            STATUS_PATH = _os.path.join(CACHE_DIR, "agent_status.json")
            INTERNAL_ANSWERS_PATH = _os.path.join(CACHE_DIR, "internal_answers.json")
            LOGS_FILE_PATH = _os.path.join(CACHE_DIR, "agent_logs.json")

            def _save_status(d):
                try:
                    _os.makedirs(CACHE_DIR, exist_ok=True)
                    with open(STATUS_PATH, "w", encoding="utf-8") as f:
                        _json.dump(d, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

            def _save_answers(d):
                try:
                    _os.makedirs(CACHE_DIR, exist_ok=True)
                    with open(INTERNAL_ANSWERS_PATH, "w", encoding="utf-8") as f:
                        _json.dump(d, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

            def _save_filtered(answers_dict):
                try:
                    from config import TEMPLATE_PATH, OUTPUT_PATH, TEAM_NAME, CONTACT_EMAIL
                    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                        sub = _json.load(f)
                    sub["team"] = TEAM_NAME
                    sub["contact_email"] = CONTACT_EMAIL
                    try:
                        from config import get_active_provider as _gap
                        _, pcfg = _gap()
                        sub["model"] = pcfg["models"]["pro"]
                    except Exception:
                        sub["model"] = "muse-spark-1.2-contributor"
                    for sid in sub.get("answers", {}):
                        for cid in sub["answers"][sid]:
                            src = answers_dict.get(sid, {}).get(cid)
                            if src:
                                sub["answers"][sid][cid] = {"status": src.get("status"), "actual": src.get("actual"), "evidence_txn_id": src.get("evidence_txn_id")}
                    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                        _json.dump(sub, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.warning(f"save_filtered in celery failed: {e}")

            # Celery task ID → логи
            task_id = self.request.id
            logger.info("Celery task %s started: %s", task_id, scenarios)

            # Подменяем broadcast на логгер воркера (WebSocket из воркера не доступен напрямую)
            _local_logs = []
            def _broadcast(msg: str):
                from datetime import datetime as _dt
                logger.info("[celery:%s] %s", task_id[:8], msg)
                ts = _dt.now().strftime("%H:%M:%S")
                line = f"[{ts}] {msg}"
                _local_logs.append(line)
                if len(_local_logs) > 200:
                    _local_logs.pop(0)
                try:
                    _os.makedirs(CACHE_DIR, exist_ok=True)
                    with open(LOGS_FILE_PATH, "w", encoding="utf-8") as f:
                        _json.dump(_local_logs, f, ensure_ascii=False)
                except Exception:
                    pass

            set_log_callback(_broadcast)

            # локальный статус для файла
            local_status = {
                "state": "running",
                "current_scenario": None,
                "progress": 0,
                "total": len(scenarios),
                "started_at": datetime.now().isoformat(),
                "initiator": initiator,
                "celery_task_id": task_id,
                "executor": "celery",
            }
            _save_status(local_status)
            local_answers = {}

            start_time = datetime.now()
            try:
                for state in run_agent_stream(scenarios):
                    if state.get("current_scenario"):
                        local_status["current_scenario"] = state["current_scenario"]
                    pending = state.get("pending_scenarios", [])
                    local_status["progress"] = local_status["total"] - len(pending) if local_status.get("total") else 0
                    if state.get("answers"):
                        local_answers.update(state["answers"])
                        _save_answers(local_answers)
                        _save_filtered(local_answers)
                    _save_status(local_status)
                    # обновляем Celery state для polling
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "current_scenario": local_status.get("current_scenario"),
                            "progress": local_status.get("progress"),
                            "total": local_status.get("total"),
                        },
                    )

                duration = (datetime.now() - start_time).total_seconds()

                # Метрики — честно через локальный evaluator
                try:
                    eval_result = evaluate(local_answers)
                    providers = load_providers()
                    active = next((p for p in providers.values() if p.get("enabled") and p.get("api_key")), {})
                    if not active:
                        active = next((p for p in providers.values() if p.get("enabled")), {})
                    # history пишем в файл напрямую (shared)
                    import json as _jh
                    HISTORY_PATH = _os.path.join(CACHE_DIR, "history.json")
                    try:
                        hist = []
                        if _os.path.exists(HISTORY_PATH):
                            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                                hist = _json.load(f)
                        hist.append(
                            {
                                "timestamp": datetime.now().isoformat(),
                                "model": active.get("models", {}).get("fast", "unknown"),
                                "scenarios": scenarios,
                                "duration_sec": duration,
                                "correct": eval_result.get("total_score", 0),
                                "total": eval_result.get("max_score", 0),
                                "accuracy": f"{eval_result.get('percentage', 0):.1f}%",
                                "initiator": initiator,
                                "executor": "celery",
                                "task_id": task_id,
                            }
                        )
                        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                            _json.dump(hist, f, ensure_ascii=False, indent=2)
                    except Exception as he:
                        logger.warning(f"history save failed: {he}")
                except Exception as e:
                    logger.warning("Metrics failed in celery: %s", e)

                local_status["state"] = "done"
                _save_status(local_status)
                _broadcast("🏁 Celery finished successfully!")
                return {"status": "done", "scenarios": scenarios, "duration": duration}

            except Exception as e:
                local_status["state"] = "error"
                local_status["error"] = str(e)
                _save_status(local_status)
                _broadcast(f"❌ Celery error: {e}")
                logger.exception("Celery agent error")
                raise

        @celery_app.task(name="app.tasks.cert_renew_check")
        def cert_renew_check():
            """Ежесуточная проверка TLS для halyk.wit.kz — автоматический certbot renew."""
            import subprocess
            import os
            from pathlib import Path

            domain = os.getenv("HALYK_DOMAIN", "halyk.wit.kz")
            cert_path = Path(f"/etc/letsencrypt/live/{domain}/fullchain.pem")

            logger.info("Cert check for %s: %s", domain, cert_path)

            # 1) Если certbot уже настроен на хосте — просто вызываем renew (идемпотентно)
            #    В Docker-контейнере certbot может не быть — тогда проверяем срок и логируем
            try:
                # Пробуем certbot renew --quiet (если установлен)
                result = subprocess.run(
                    ["certbot", "renew", "--quiet", "--deploy-hook", "nginx -s reload"],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    logger.info("certbot renew OK (или нечего обновлять)")
                    return {"status": "renew_checked", "domain": domain}
                else:
                    logger.warning("certbot renew rc=%s: %s", result.returncode, result.stderr[:500])
            except FileNotFoundError:
                logger.info("certbot not in container — проверяем срок существующего сертификата")
            except Exception as e:
                logger.warning("certbot renew failed: %s", e)

            # 2) Fallback: проверка срока через openssl (если сертификат уже смонтирован)
            if cert_path.exists():
                try:
                    out = subprocess.run(
                        ["openssl", "x509", "-enddate", "-noout", "-in", str(cert_path)],
                        capture_output=True, text=True, timeout=10,
                    )
                    logger.info("Cert expiry: %s", out.stdout.strip())
                    return {"status": "expiry_checked", "expiry": out.stdout.strip()}
                except Exception as e:
                    logger.warning("openssl check failed: %s", e)

            logger.info("Cert check done — хостовый celery продолжит свою логику автогенерации")
            return {"status": "checked", "domain": domain}

except ImportError:
    pass
