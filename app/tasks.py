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
            from config import load_providers
            from app.main import agent_status, agent_answers, agent_logs, broadcast_log, run_history, save_history

            # Celery task ID → логи
            task_id = self.request.id
            logger.info("Celery task %s started: %s", task_id, scenarios)

            # Подменяем broadcast на логгер воркера (WebSocket из воркера не доступен напрямую)
            def _broadcast(msg: str):
                logger.info("[celery:%s] %s", task_id[:8], msg)
                # также кладём в общий лог, который читает API
                try:
                    broadcast_log(msg)
                except Exception:
                    pass

            set_log_callback(_broadcast)

            agent_status["state"] = "running"
            agent_status["current_scenario"] = None
            agent_status["progress"] = 0
            agent_status["started_at"] = datetime.now().isoformat()
            agent_status["initiator"] = initiator
            agent_status["celery_task_id"] = task_id

            start_time = datetime.now()
            try:
                for state in run_agent_stream(scenarios):
                    if state.get("current_scenario"):
                        agent_status["current_scenario"] = state["current_scenario"]
                    pending = state.get("pending_scenarios", [])
                    agent_status["progress"] = agent_status["total"] - len(pending) if agent_status.get("total") else 0
                    if state.get("answers"):
                        agent_answers.update(state["answers"])
                    # обновляем Celery state для polling
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "current_scenario": agent_status.get("current_scenario"),
                            "progress": agent_status.get("progress"),
                            "total": agent_status.get("total"),
                        },
                    )

                duration = (datetime.now() - start_time).total_seconds()

                # Метрики — честно через локальный evaluator
                try:
                    eval_result = evaluate(agent_answers)
                    providers = load_providers()
                    active = next((p for p in providers.values() if p.get("enabled") and p.get("api_key")), {})
                    if not active:
                        active = next((p for p in providers.values() if p.get("enabled")), {})
                    run_history.append(
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
                    save_history()
                except Exception as e:
                    logger.warning("Metrics failed in celery: %s", e)

                agent_status["state"] = "done"
                _broadcast("🏁 Celery finished successfully!")
                return {"status": "done", "scenarios": scenarios, "duration": duration}

            except Exception as e:
                agent_status["state"] = "error"
                agent_status["error"] = str(e)
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
