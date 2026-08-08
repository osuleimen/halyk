# Halyk Covenant Agent — Muse Spark + Gemini Vision

Честный, воспроизводимый AI-агент для проверки ковенантов (12 сценариев × 3 ковенанта = 36 ячеек) на `halyk.wit.kz`.

**Стек:** FastAPI + LangGraph ReAct + Muse Spark (primary, `muse-spark-1.2-contributor`) + Gemini Vision fallback (`gemini-3.5-flash-lite` → `3.5-flash`), Celery/Redis, Caddy, Docker.

## Быстрый старт

```bash
cp deploy/.env.example .env  # ONAIU_NETWORK, HALYK_PORT, ADMIN_TOKEN
docker compose up -d --build
curl -s http://127.0.0.1:18080/health | jq .   # ok
curl -s http://127.0.0.1:18080/api/status | jq .
```

UI: `https://halyk.wit.kz` — `muse-spark` badge + `Gemini Vision` fallback, pipeline `PyMuPDF→Vision→ReAct→verify`, мобила `💬` Halyk AI.

## Провайдеры

`Провайдеры` → `Muse Spark API key` → `Save` → `Test`. `Gemini` нужен только для сканированных финотчётов (Vision, `<1200` симв.). `muse_spark` приоритет, `gemini` не используется в чате.

## Безопасность

* `slowapi` 60/min default, `10/min` чат, `3/min` run, `ADMIN_TOKEN` (`X-Admin-Token`) для `/api/run|upload|providers`
* `CORS` + `TrustedHost` + `ZipSlip` + `50MB` лимит
* `ADMIN_TOKEN=$(openssl rand -hex 16)` в `.env`

## Наблюдаемость

* `GET /health` — ledger/cache/celery, `GET /metrics` — Prometheus `halyk_requests_total`, `halyk_request_duration_seconds`
* `X-Request-ID` в каждом ответе, JSON логи

## Разработка

```bash
make install lint format test
make build run logs health
```

CI: `.github/workflows/ci.yml` — `black, ruff, mypy, pytest, docker build, health`.

## Честный скор

```bash
python score.py  # strict: P1 3/3, P6 3/3 после verify
# weighted: 86.11% (31/36) — P3.6.1/P5.6.1 требуют Vision таблиц
```
