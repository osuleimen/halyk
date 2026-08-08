"""
Agent Tools — @tool functions that the LangGraph ReAct agent can call.

These tools give the agent access to documents, the ledger, and answer submission.
The agent decides WHICH tools to call and in WHAT ORDER.
"""
from __future__ import annotations
import json
import logging
from typing import Optional, Union
from langchain_core.tools import tool

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from app.services import document_store, ledger
from config import ACCOUNT_TO_SCENARIO, SCENARIO_TO_ACCOUNT

logger = logging.getLogger(__name__)

# ── Runtime state holders (set before agent runs) ──
_extracted_texts: dict[str, str] = {}
_document_index: dict[str, dict] = {}
_answers: dict[str, dict] = {}


def set_runtime_data(texts: dict, index: dict, answers: dict):
    """Inject runtime data into tools (called before agent run)."""
    global _extracted_texts, _document_index, _answers
    _extracted_texts = texts
    _document_index = index
    _answers = answers


def get_answers() -> dict:
    return _answers


# ═══════════════════════════════════════
# Tool 1: list_documents
# ═══════════════════════════════════════

@tool
def list_documents(
    scenario_id: Optional[str] = None,
    doc_type: Optional[str] = None,
) -> str:
    """Показать список документов с фильтрацией.

    Args:
        scenario_id: ID заёмщика (P1, P2, ..., B1, B4). Если None — все документы.
        doc_type: Тип документа: loan_agreement, financial_statement, audit_report, kyc_dossier, treasury_report, other. Если None — все типы.

    Returns:
        Список документов с метаданными.
    """
    results = []
    for fname, meta in _document_index.items():
        if scenario_id and scenario_id not in meta.get("scenario_ids", []):
            continue
        if doc_type and meta.get("doc_type") != doc_type:
            continue
        if not meta.get("is_current", True):
            continue
        results.append({
            "filename": fname,
            "doc_type": meta.get("doc_type"),
            "scenario_ids": meta.get("scenario_ids", []),
            "company_name": meta.get("company_name", ""),
            "period": meta.get("period", ""),
            "summary": meta.get("summary", ""),
        })

    if not results:
        return f"Документы не найдены (scenario={scenario_id}, type={doc_type})."

    lines = [f"Найдено {len(results)} документов:"]
    for r in results:
        lines.append(
            f"  📄 {r['filename']}  |  {r['doc_type']}  |  "
            f"scenarios={r['scenario_ids']}  |  {r['company_name']}  |  {r['period']}"
        )
        if r["summary"]:
            lines.append(f"     └─ {r['summary']}")
    return "\n".join(lines)


# ═══════════════════════════════════════
# Tool 2: read_document
# ═══════════════════════════════════════

@tool
def read_document(filename: str) -> str:
    """Прочитать полный текст документа по имени файла.

    Args:
        filename: Имя файла (например '1d262694c308.pdf').

    Returns:
        Полный текст документа.
    """
    text = _extracted_texts.get(filename)
    if text is None:
        return f"Ошибка: документ '{filename}' не найден. Используй list_documents() чтобы увидеть доступные файлы."
    if len(text) > 30000:
        return text[:30000] + f"\n\n... [текст обрезан, показано 30000 из {len(text)} символов]"
    return text


# ═══════════════════════════════════════
# Tool 3: query_ledger
# ═══════════════════════════════════════

@tool
def query_ledger(
    scenario_id: str,
    description_contains: Optional[str] = None,
    counterparty_contains: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
) -> str:
    """Запросить транзакции из леджера (master_ledger_2025.csv).

    Args:
        scenario_id: ID заёмщика (P1, P2, ..., B1, B4). Обязательный.
        description_contains: Фильтр по описанию (подстрока, без учёта регистра).
        counterparty_contains: Фильтр по контрагенту (подстрока).
        date_from: Начало периода (YYYY-MM-DD).
        date_to: Конец периода (YYYY-MM-DD).
        min_amount: Минимальная абсолютная сумма.
        max_amount: Максимальная абсолютная сумма.

    Returns:
        Таблица транзакций + сводка.
    """
    df = ledger.query(
        scenario_id=scenario_id,
        description_contains=description_contains,
        counterparty_contains=counterparty_contains,
        date_from=date_from,
        date_to=date_to,
        min_amount=min_amount,
        max_amount=max_amount,
    )
    formatted = ledger.format_transactions_for_llm(df)

    # Add summary
    if not df.empty:
        total_sum = df["amount"].sum()
        total_abs = df["abs_amount"].sum()
        formatted += f"\n\nСумма (с учётом знака): {total_sum:,.2f}"
        formatted += f"\nСумма (по модулю): {total_abs:,.2f}"

    return formatted


# ═══════════════════════════════════════
# Tool 4: query_ledger_sql
# ═══════════════════════════════════════

@tool
def query_ledger_sql(query: str) -> str:
    """Выполнить SQL-запрос к леджеру (таблица 'ledger').
    
    Схема таблицы:
    txn_id (TEXT), date (TEXT), account_id (TEXT), counterparty (TEXT), description (TEXT), amount (REAL), currency (TEXT), scenario_id (TEXT), abs_amount (REAL)
    
    Используйте это для точных математических операций (SUM, AVG) или сложной фильтрации, чтобы избежать ошибок LLM при вычислениях. 
    Например: SELECT SUM(amount) FROM ledger WHERE scenario_id = 'P1' AND amount < 0 AND txn_id NOT IN ('TXN-P1-0010')
    """
    import sqlite3
    import pandas as pd
    
    try:
        logger.info(f"  Tool query_ledger_sql called with query: {query}")
        df = ledger._get_df()
        conn = sqlite3.connect(':memory:')
        df.to_sql('ledger', conn, index=False)
        
        result_df = pd.read_sql(query, conn)
        conn.close()
        
        if result_df.empty:
            return "Результат пуст (0 строк)."
            
        return result_df.to_string()
    except Exception as e:
        return f"Ошибка выполнения SQL: {e}"


# ═══════════════════════════════════════
# Tool 5: find_related_parties
# ═══════════════════════════════════════

@tool
def find_related_parties(scenario_id: str) -> str:
    """Найти связанные/аффилированные стороны заёмщика из KYC-досье.

    Критично для ковенантов о платежах связанным сторонам.

    Args:
        scenario_id: ID заёмщика.

    Returns:
        Список связанных сторон или инструкция прочитать KYC-досье.
    """
    # Find KYC dossier
    kyc_docs = [
        fname for fname, meta in _document_index.items()
        if scenario_id in meta.get("scenario_ids", [])
        and meta.get("doc_type") in ("kyc_dossier", "audit_report")
        and meta.get("is_current", True)
    ]

    if not kyc_docs:
        return (
            f"KYC-досье для {scenario_id} не найдено в индексе. "
            f"Попробуй list_documents(scenario_id='{scenario_id}') и поищи документы "
            f"со словами 'KYC', 'связанные стороны', 'аффилированные' вручную."
        )

    # Read KYC docs and extract related parties
    lines = [f"Документы KYC/аудит для {scenario_id}:"]
    for fname in kyc_docs:
        text = _extracted_texts.get(fname, "")
        lines.append(f"\n📄 {fname}:")
        # Extract sections about related parties
        lower = text.lower()
        keywords = ["связанн", "аффилированн", "related part", "бенефициар", "доля голосующих прав"]
        for keyword in keywords:
            idx = 0
            while True:
                idx = lower.find(keyword, idx)
                if idx == -1:
                    break
                # Extract surrounding context
                start = max(0, idx - 100)
                end = min(len(text), idx + 1000)
                lines.append(f"  [{keyword}]: ...{text[start:end]}...")
                idx += len(keyword)

    return "\n".join(lines) if len(lines) > 1 else f"Связанные стороны в KYC-досье {scenario_id} не найдены."


# ═══════════════════════════════════════
# Tool 5: calculate_metric
# ═══════════════════════════════════════

@tool
def calculate_metric(
    formula: str,
    values: str,
) -> str:
    """Рассчитать финансовый показатель.

    Args:
        formula: Тип расчёта. Одно из:
            - "ratio": отношение numerator/denominator
            - "sum": сумма списка чисел
            - "difference": разница a - b
            - "expression": произвольное арифметическое выражение (безопасный eval)
        values: JSON-строка с параметрами. Примеры:
            - для ratio: {"numerator": 1000, "denominator": 500}
            - для sum: {"numbers": [100, 200, 300]}
            - для difference: {"a": 1000, "b": 300}
            - для expression: {"expr": "1000000 / (500000 - 200000)"}

    Returns:
        Результат вычисления.
    """
    try:
        params = json.loads(values)
    except json.JSONDecodeError:
        return f"Ошибка: невалидный JSON в values: {values}"

    try:
        if formula == "ratio":
            num = float(params["numerator"])
            den = float(params["denominator"])
            if den == 0:
                return "Ошибка: деление на ноль"
            result = num / den
        elif formula == "sum":
            result = sum(float(x) for x in params["numbers"])
        elif formula == "difference":
            result = float(params["a"]) - float(params["b"])
        elif formula == "expression":
            expr = params["expr"]
            # Safe eval: only allow digits, operators, parens, dots
            import re
            if not re.match(r'^[\d\s\+\-\*/\(\)\.\,]+$', expr):
                return f"Ошибка: небезопасное выражение: {expr}"
            result = eval(expr.replace(",", ""))
        else:
            return f"Неизвестная формула: {formula}. Доступны: ratio, sum, difference, expression"

        return f"Результат: {result:.6f} (округлённо до 2 знаков: {round(abs(result), 2)})"
    except Exception as e:
        return f"Ошибка вычисления: {e}"


# ═══════════════════════════════════════
# Tool 6: submit_answer
# ═══════════════════════════════════════

@tool
def submit_answer(
    scenario_id: str,
    covenant_id: str,
    status: str,
    actual: Union[float, str],
    evidence_txn_id: Optional[str] = None,
    reasoning: Optional[str] = None,
    graph_mermaid: Optional[str] = None,
) -> str:
    """Зафиксировать ответ по ковенанту.

    Args:
        scenario_id: ID заёмщика (P1, P2, ..., B1, B4).
        covenant_id: Номер ковенанта (6.1, 6.2 или 6.3).
        status: COMPLIANT или BREACH (заглавными).
        actual: Фактическое значение показателя (положительное, 2 знака после запятой).
        evidence_txn_id: ID транзакции-улики (TXN-XX-XXXX) или None.
        reasoning: Объяснение/вывод, почему принят такой статус (человекочитаемый комментарий).

    Returns:
        Подтверждение записи.
    """
    # Validate
    if status not in ("COMPLIANT", "BREACH"):
        return f"Ошибка: status должен быть 'COMPLIANT' или 'BREACH', получено '{status}'"
    if covenant_id not in ("6.1", "6.2", "6.3"):
        return f"Ошибка: covenant_id должен быть '6.1', '6.2' или '6.3', получено '{covenant_id}'"
    
    try:
        actual_val = float(actual)
    except ValueError:
        return f"Ошибка: actual должно быть числом, получено '{actual}'"
        
    if actual_val < 0:
        return f"Ошибка: actual должен быть положительным, получено {actual_val}"

    actual_rounded = round(abs(actual_val), 2)

    if scenario_id not in _answers:
        _answers[scenario_id] = {}

    _answers[scenario_id][covenant_id] = {
        "status": status,
        "actual": actual_rounded,
        "evidence_txn_id": evidence_txn_id,
        "reasoning": reasoning,
        "graph_mermaid": graph_mermaid,
    }

    logger.info("Answer saved: %s/%s = %s (actual=%.2f, evidence=%s, reasoning=%s)",
                scenario_id, covenant_id, status, actual_rounded, evidence_txn_id, bool(reasoning))

    return (
        f"✅ Ответ записан: {scenario_id} / {covenant_id}\n"
        f"   status: {status}\n"
        f"   actual: {actual_rounded}\n"
        f"   evidence_txn_id: {evidence_txn_id}\n"
        f"   reasoning: {reasoning}"
    )


# ═══════════════════════════════════════
# Tool 7: request_human_review
# ═══════════════════════════════════════

@tool
def request_human_review(transaction_id: str, question: str) -> str:
    """Запросить у человека ревью сомнительной транзакции (Human-in-the-Loop).

    Используй это, если есть неоднозначность в отнесении транзакции к определенной статье (например, социальный налог в P6).
    Агент приостановит работу и дождется решения человека через дашборд.

    Args:
        transaction_id: ID транзакции (например, TXN-P6-0034).
        question: Вопрос к аналитику (например, "Включать ли социальный налог в операционные расходы?").

    Returns:
        Решение человека (строка).
    """
    import requests
    try:
        logger.info(f"⏸ Запрос ревью человека для {transaction_id}: {question}")
        # Вызов API локального сервера. Агент блокируется (работает в фоновом потоке), 
        # пока человек не нажмёт кнопку в UI.
        resp = requests.post(
            "http://127.0.0.1:8000/api/hitl/request", 
            json={"transaction_id": transaction_id, "question": question},
            timeout=3600 # Ждем до часа
        )
        if resp.status_code == 200:
            decision = resp.json().get("decision")
            logger.info(f"▶️ Получен ответ человека: {decision}")
            return f"Ответ от человека: {decision}"
        else:
            return f"Ошибка API: {resp.status_code} {resp.text}"
    except Exception as e:
        return f"Ошибка при запросе человека: {e}"


# ═══════════════════════════════════════
# All tools list (for LangGraph)
# ═══════════════════════════════════════

ALL_TOOLS = [
    list_documents,
    read_document,
    query_ledger,
    query_ledger_sql,
    find_related_parties,
    calculate_metric,
    submit_answer,
    request_human_review,
]
