# Halyk Covenant Agent — halyk.wit.kz

Честный AI-агент для проверки ковенантов банка: **12 сценариев × 3 ковенанта = 36 ячеек**. Находит документы, считает показатели в USD, ищет транзакцию-улику и формирует `submission.json` с доказательствами. Стек: `FastAPI + LangGraph ReAct + Muse Spark (primary) + Gemini Vision fallback + Redis/Celery`.

---

## Как пользоваться — веб на halyk.wit.kz

1. Открой `https://halyk.wit.kz`
2. Вверху видишь `muse-spark-1.2 primary` + `Gemini Vision fallback` и статус `IDLE/RUNNING/DONE`
3. **Если зависло `running`:** нажми `🧹 Сброс` в шапке (чистит кэш и статус, `submission.json` сохраняет). Для полной очистки — `POST /api/reset_all?hard=1`
4. **Загрузить датасет:** `📁 ZIP` или перетащи `*.zip` на дроп-зону в артефактах (≤50MB, защита ZipSlip). После загрузки выбери что запустить — **не стартует сам**.
5. **Запустить:** `▶ Запустить` — все 12, или `Быстрый тест P1–P3`, или в чате `запусти P6` / `запусти P1,P2,B1`. Полоска прогресса + пайплайн 6 шагов подсвечивается.
6. **Следить:** вкладка `Процесс` → `Сейчас: P6 • 3/12` + `Логи агента (live)` 140px — весь поток `extract → classify → query_ledger_sql → submit_answer`. В чате — только важные `✅ BREACH/COMPLIANT`.
7. **Результаты:** по `🏁 finished` артефакты сами переключаются на `Результаты` — таблица 12×3, клик по строке → `Комментарий агента` (reasoning) + в чат. `Пересчитать` — `weighted 0.50/0.30/0.20`.
8. **Скачать:** в `Формат` → `Скачать ZIP (2 файла)`:
   - `submission.json` — **чистый, 3 поля на ячейку** (`status`, `actual`, `evidence_txn_id`) — для сдачи по шаблону
   - `submission_with_reasoning.json` — внутренний, с `reasoning`/`graph_mermaid` для проверки логики
   - Отдельно: `GET /api/submission` (чистый) и `GET /api/submission_with_reasoning` (полный)
9. Границу между чатом и артефактами тяни за `⋮` посередине (сохраняется), `dblclick` — сброс. На мобиле — стек.

**Чат-команды:** `запусти все`, `запусти P6`, `покажи нарушения`, `объясни P6.6.2`, `скачай submission`

---

## Локальный запуск

```bash
cp deploy/.env.example .env
# заполни MUSE_SPARK_API_KEY, GEMINI_API_KEY (Vision), ADMIN_TOKEN=$(openssl rand -hex 16)
docker compose up -d --build
curl -s http://127.0.0.1:18080/health | jq .      # ok
curl -s http://127.0.0.1:18080/api/status | jq .
# первый запуск может занять 2-4 мин (Vision для сканов)
```

Если `Permission denied /app/cache/...`:
```bash
docker exec -u 0 halyk-covenant-agent chown -R halyk:halyk /app/cache
docker exec -u 0 halyk-covenant-agent chmod -R 777 /app/cache
docker compose restart
# в образе уже есть deploy/entrypoint.sh который чинит права при старте
```

## Провайдеры

`⚙️` → `Muse Spark API key` (LLM_...) → `Сохранить` → `Test`. `GEMINI_API_KEY` нужен только для сканов Vision (`<1200` симв. или таблицы). Приоритет `muse_spark`, `gemini` в чате не используется.

Без ключа `POST /api/run` вернет `400` с подсказкой `Открой ⚙️`.

## Что внутри агента

**6 шагов:** 1. Архив `master_ledger_2025.csv` + PDF → 2. Классификация `account_id → scenario_id` → 3. `PyMuPDF → Gemini Vision` (если `<1200` симв. или нет цифр) → 4. Связи `txn_id = TXN-{scenario}-*` + KYC `≥20-40%` → 5. `query_ledger_sql` `SUM` в USD + верификация `P3/P4/P5/P6/P9` → 6. `submission.json` (3 поля) + внутренний с reasoning.

**Сдача:** `submission.json` строго по `agentic-bank-public/submission_template.json`:
```json
"P6": {
  "6.1": { "status": "BREACH", "actual": 0.10, "evidence_txn_id": "TXN-P6-0040" },
  "6.2": { "status": "COMPLIANT", "actual": 3.64, "evidence_txn_id": null }
}
```
`reasoning`/`graph_mermaid` — только в `submission_with_reasoning.json` и в UI, не в сдаче.

**Оценка:** `status 0.50` (точно), `actual 0.30` (шкала `1 - e/0.05`), `evidence 0.20` (точный `txn` или шкала от `actual` если `null` в ключе). `status` неверно → 0 за ячейку.

## API

* `POST /api/run` `{scenarios?:["P6"]}` → `threading` (по умолчанию, `USE_CELERY=1` для Celery)
* `POST /api/reset` → `idle`, `POST /api/reset_all` / `?hard=1` — общий ресет
* `POST /api/upload` `multipart ZIP` → распаковка + очистка кэша
* `GET /api/submission` (чистый), `GET /api/submission_with_reasoning` (полный), `GET /api/download_submission` → ZIP 2 файла
* `GET /api/status`, `GET /api/answers`, `GET /api/evaluate`, `GET /api/logs`, `WS /ws/logs`
* `GET /health` (ledger/cache/celery), `GET /metrics` Prometheus, `X-Request-ID`

Безопасность: `slowapi 60/min` (чат 10, run 3, upload 5), `ADMIN_TOKEN` → `X-Admin-Token`, `CORS *`, `ZipSlip` + 50MB.

## Разработка

```bash
make install lint format test   # black ruff mypy pytest
make build run logs health
python score.py  # strict P1 3/3, P6 3/3 после verify, weighted 86% — P3/P5 требуют Vision
```

CI: `.github/workflows/ci.yml` — lint + pytest + docker build + health.
