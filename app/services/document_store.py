"""
Document Store — extraction, indexing, and caching of PDF documents.

Strategy: PyMuPDF first (fast, free) → Gemini Vision fallback (for poor extractions).
Uses multi-provider config for LLM classification.
"""
import os
import re
import json
import time
import logging
import fitz  # PyMuPDF

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from config import (
    DOCUMENTS_DIR, CACHE_DIR, ACCOUNT_TO_SCENARIO,
    get_active_provider,
)
from app.services.llm_factory import create_vision_llm

logger = logging.getLogger(__name__)

EXTRACT_CACHE_PATH = os.path.join(CACHE_DIR, "extracted_texts.json")
INDEX_CACHE_PATH = os.path.join(CACHE_DIR, "document_index.json")

# ──────────────────────────────────────────────
# Text extraction
# ──────────────────────────────────────────────

def _extract_pymupdf(filepath: str) -> str:
    """Fast local extraction via PyMuPDF."""
    doc = fitz.open(filepath)
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages).strip()


def _is_scanned_pdf(filepath: str) -> bool:
    """Auto-detect scanned PDFs (images, no text)."""
    doc = fitz.open(filepath)
    total_text = 0
    pages_to_check = min(3, len(doc))
    for i in range(pages_to_check):
        total_text += len(doc[i].get_text().strip())
    doc.close()
    return total_text < 200


def _quality_ok(text: str, filepath: str) -> bool:
    """Heuristic: is the local extraction good enough?"""
    if len(text) < 100:
        return False
    # If it's a loan agreement but missing 6.1/6.2/6.3
    lower = text.lower()
    is_loan = "ковенант" in lower or "договор банковского займа" in lower or "кредитн" in lower
    if is_loan:
        has_sections = sum(1 for s in ["6.1", "6.2", "6.3"] if s in text)
        if has_sections < 2:
            return False  # Lost covenant sections → need Vision
    return True


GEMINI_EXTRACT_PROMPT = """Извлеки ВЕСЬ текст из этого документа полностью и точно.

Включи:
- Все заголовки, параграфы, пункты и подпункты
- Все таблицы (сохрани структуру в markdown-формате)
- Все числа, даты, суммы — точно как в документе
- Все идентификаторы (ACC-XXXX, TXN-XXXX, KYC-XXXX)

Особенно важны: пункты 6.1, 6.2, 6.3 (ковенанты), финансовые показатели, формулы.
Верни только текст документа, без своих комментариев."""


def _extract_gemini_vision(filepath: str, max_retries: int = 3) -> str:
    """Fallback: send PDF to Gemini Vision for OCR/extraction."""
    try:
        pid, pcfg = get_active_provider()
        vision_client = create_vision_llm(pid, pcfg)
        if vision_client is None:
            logger.warning("No vision-capable provider available")
            return ""
    except Exception as e:
        logger.warning("Cannot get vision provider: %s", e)
        return ""

    from google.genai import types
    model = pcfg["models"]["fast"]

    for attempt in range(max_retries):
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            response = vision_client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=data, mime_type="application/pdf"),
                    GEMINI_EXTRACT_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=65536,
                ),
            )
            return response.text.strip()
        except Exception as e:
            logger.warning("Vision extraction attempt %d failed for %s: %s", attempt + 1, filepath, e)
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait = min(60, 2 ** (attempt + 2))
                logger.info("Rate limited, waiting %ds...", wait)
                time.sleep(wait)
            elif attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
    return ""


def extract_document(filepath: str) -> tuple[str, str]:
    """Extract text: PyMuPDF first, Gemini Vision fallback.
    
    Returns: (text, method) where method is 'local' or 'vision'
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".txt", ".csv"):
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), "local"

    # Step 1: try local
    text = _extract_pymupdf(filepath)
    if _quality_ok(text, filepath):
        return text, "local"

    # Step 2: Gemini Vision fallback
    logger.info("PyMuPDF quality low for %s, trying vision...", os.path.basename(filepath))
    gemini_text = _extract_gemini_vision(filepath)
    if gemini_text:
        return gemini_text, "vision"

    return text, "local_fallback"  # worst case return local


def extract_all(use_cache: bool = True, progress_callback=None) -> dict[str, str]:
    """Extract text from all documents. Returns {filename: text}."""
    if use_cache and os.path.exists(EXTRACT_CACHE_PATH):
        with open(EXTRACT_CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
        logger.info("Loaded %d cached extractions", len(cached))
        return cached

    results = {}
    files = sorted(
        f for f in os.listdir(DOCUMENTS_DIR)
        if f.endswith((".pdf", ".csv", ".txt")) and f != "Thumbs.db"
    )
    total = len(files)
    vision_count = 0

    for i, fname in enumerate(files):
        path = os.path.join(DOCUMENTS_DIR, fname)
        text, method = extract_document(path)
        results[fname] = text

        if method == "vision":
            vision_count += 1
            time.sleep(2)  # rate limit for vision API

        if progress_callback:
            progress_callback(i + 1, total, fname, method)
        else:
            logger.info("[%d/%d] %s (%s, %d chars)", i + 1, total, fname, method, len(text))

        if (i + 1) % 20 == 0:
            with open(EXTRACT_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False)

    with open(EXTRACT_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)
    logger.info("Extraction done: %d docs (%d via vision)", total, vision_count)
    return results


# ──────────────────────────────────────────────
# Document classification & indexing
# ──────────────────────────────────────────────

CLASSIFY_PROMPT = """Проанализируй текст документа и верни JSON:
{
  "doc_type": "loan_agreement" | "financial_statement" | "audit_report" | "kyc_dossier" | "treasury_report" | "other",
  "account_ids": ["ACC-XXXX", ...],
  "company_name": "...",
  "period": "2025" | "2024" | ...,
  "is_current": true/false,
  "summary": "краткое описание (1-2 предложения)"
}

Правила:
- loan_agreement: кредитный договор с ковенантами (пункты 6.1/6.2/6.3)
- financial_statement: финансовая отчётность, примечания, баланс
- audit_report: аудиторский отчёт, расчёт ковенантов
- kyc_dossier: KYC досье, связанные стороны, комплаенс
- treasury_report: казначейский отчёт, движение средств
- other: политики, SOP, маркетинг, IT, переписка
- is_current=false если "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ" или "НЕ ПРИМЕНЯТЬ"
- Верни ТОЛЬКО JSON"""


def classify_document(filename: str, text: str) -> dict:
    """Classify a document using the active LLM provider."""
    # Normalize Cyrillic homoglyphs before regex
    cyrillic = "АВСЕНКМОРТХУаеорсх"
    latin    = "ABCENKMOPTXYaeopcx"
    trans = str.maketrans(cyrillic, latin)
    norm_text = text.translate(trans)
    
    # Quick regex pre-extraction
    account_ids = list(set(re.findall(r"ACC-\d+", norm_text)))
    scenario_ids = [ACCOUNT_TO_SCENARIO[a] for a in account_ids if a in ACCOUNT_TO_SCENARIO]

    lower = text.lower()
    is_outdated = "недействующая" in lower or "не применять" in lower

    # Fast-path for obviously irrelevant docs
    if not account_ids and not any(kw in lower for kw in ["ковенант", "финансов", "аудитор", "kyc", "досье", "баланс", "отчёт"]):
        return {
            "filename": filename, "doc_type": "other",
            "account_ids": [], "scenario_ids": [],
            "company_name": "", "period": "", "is_current": not is_outdated,
            "summary": "Нерелевантный документ",
        }

    # Use LLM for classification
    truncated = text[:12000]
    try:
        pid, pcfg = get_active_provider()
        from app.services.llm_factory import create_llm
        from langchain_core.messages import SystemMessage, HumanMessage
        llm = create_llm(pid, pcfg, tier="fast", max_tokens=512)
        
        messages = [
            SystemMessage(content="You are a document classifier. You MUST return ONLY valid JSON without any markdown formatting or extra text."),
            HumanMessage(content=f"{CLASSIFY_PROMPT}\n\nДокумент: {filename}\n\n{truncated}")
        ]
        response = llm.invoke(messages)
        # Parse JSON from response
        content = response.content
        # Try to extract JSON from response
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
            
        content = content.strip()
        if content.startswith('{'):
            result = json.loads(content)
        else:
            raise ValueError(f"No JSON found in response: {content[:100]}")
    except Exception as e:
        logger.warning("Classification failed for %s: %s", filename, e)
        # Heuristic fallback
        doc_type = "other"
        if "ковенант" in lower or "6.1" in text:
            doc_type = "loan_agreement"
        elif "баланс" in lower or "отчётност" in lower:
            doc_type = "financial_statement"
        elif "аудитор" in lower:
            doc_type = "audit_report"
        elif "kyc" in lower or "досье" in lower:
            doc_type = "kyc_dossier"
        result = {"doc_type": doc_type, "company_name": "", "period": "", "is_current": True, "summary": "heuristic classification"}

    result["filename"] = filename
    result.setdefault("account_ids", account_ids)
    for a in account_ids:
        if a not in result["account_ids"]:
            result["account_ids"].append(a)
    result["scenario_ids"] = list(set(
        ACCOUNT_TO_SCENARIO[a] for a in result.get("account_ids", []) if a in ACCOUNT_TO_SCENARIO
    ))
    if is_outdated:
        result["is_current"] = False

    return result


def build_index(texts: dict[str, str], use_cache: bool = True, progress_callback=None) -> dict[str, dict]:
    """Classify all documents and build index. Returns {filename: meta}."""
    if use_cache and os.path.exists(INDEX_CACHE_PATH):
        with open(INDEX_CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
        logger.info("Loaded %d cached classifications", len(cached))
        return cached

    index = {}
    filenames = sorted(texts.keys())
    total = len(filenames)

    for i, fname in enumerate(filenames):
        meta = classify_document(fname, texts[fname])
        index[fname] = meta

        if progress_callback:
            progress_callback(i + 1, total, fname, meta.get("doc_type", "?"))
        else:
            logger.info("[%d/%d] %s → %s  scenarios=%s", i + 1, total, fname,
                        meta.get("doc_type"), meta.get("scenario_ids"))

        # Rate limit
        if meta.get("doc_type") != "other":
            time.sleep(0.3)

        if (i + 1) % 20 == 0:
            with open(INDEX_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False)

    with open(INDEX_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    logger.info("Indexing done: %d documents", total)
    return index


def get_docs_for_scenario(index: dict, scenario_id: str, doc_type: str | None = None) -> list[dict]:
    """Get documents relevant to a scenario."""
    results = []
    for fname, meta in index.items():
        if scenario_id not in meta.get("scenario_ids", []):
            continue
        if not meta.get("is_current", True):
            continue
        if doc_type and meta.get("doc_type") != doc_type:
            continue
        results.append(meta)
    return results
