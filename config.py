"""
Halyk AI Challenge — Configuration
Supports multiple LLM providers: Gemini, DeepSeek, Muse Spark
"""
import os
import json

# === Paths ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "agentic-bank-public")
DOCUMENTS_DIR = os.path.join(DATA_DIR, "documents")
LEDGER_PATH = os.path.join(DATA_DIR, "master_ledger_2025.csv")
TEMPLATE_PATH = os.path.join(DATA_DIR, "submission_template.json")
GROUND_TRUTH_PATH = os.path.join(DATA_DIR, "ground_truth.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "submission.json")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# === Team info ===
TEAM_NAME = "wit.kz"
CONTACT_EMAIL = "info@wit.kz"

# === LLM Providers ===
# Each provider is an OpenAI-compatible or native API config
PROVIDERS_PATH = os.path.join(CACHE_DIR, "providers.json")

DEFAULT_PROVIDERS = {
    "muse_spark": {
        "name": "Meta Muse Spark",
        "type": "openai_compat",
        "api_key": "",
        "base_url": "https://api.meta.ai/v1",
        "models": {
            "fast": "muse-spark-1.2-contributor",
            "pro": "muse-spark-1.2-contributor",
        },
        "enabled": True,
    },
    "gemini": {
        "name": "Google Gemini",
        "type": "gemini",              # Vision only — Lite основная, Flash fallback если Lite не справилась
        "api_key": "",
        "models": {
            "fast": "gemini-3.5-flash-lite",
            "pro": "gemini-3.5-flash",
        },
        "enabled": False,
    },
    "deepseek": {
        "name": "DeepSeek",
        "type": "openai_compat",       # OpenAI-compatible
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "models": {
            "fast": "deepseek-v4-flash",
            "pro": "deepseek-v4-flash",
        },
        "enabled": False,
    },
    "openai": {
        "name": "OpenAI",
        "type": "openai_compat",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "models": {
            "fast": "gpt-4o-mini",
            "pro": "gpt-4o",
        },
        "enabled": False,
    },
    "groq": {
        "name": "Groq (Free/Fast)",
        "type": "openai_compat",
        "api_key": "",
        "base_url": "https://api.groq.com/openai/v1",
        "models": {
            "fast": "llama-3.3-70b-versatile",
            "pro": "llama-3.3-70b-versatile",
        },
        "enabled": False,
    },
}


def _apply_env_overrides(providers: dict) -> dict:
    """ENV имеет приоритет над cache/providers.json — .env живёт в гитигноре."""
    env_map = {
        "muse_spark": "MUSE_SPARK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
    }
    for pid, env_key in env_map.items():
        if pid in providers:
            env_val = os.getenv(env_key, "").strip()
            # также поддерживаем старые имена без префикса
            if not env_val and pid == "muse_spark":
                env_val = os.getenv("MUSE_SPARK_API_KEY", "").strip() or os.getenv("LLM_API_KEY", "").strip()
            if env_val:
                providers[pid]["api_key"] = env_val
                providers[pid]["enabled"] = True
    return providers

def load_providers() -> dict:
    """Load provider configs from cache or defaults. ENV (.env) имеет приоритет."""
    if os.path.exists(PROVIDERS_PATH):
        with open(PROVIDERS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # Merge with defaults (new providers added in code)
        for k, v in DEFAULT_PROVIDERS.items():
            if k not in saved:
                saved[k] = v
        # Миграция на Gemini 3.5 Flash-Lite (основная) + 3.5 Flash fallback
        if "gemini" in saved and "models" in saved["gemini"]:
            m = saved["gemini"]["models"]
            # всё старое (1.5, 2.0, 2.5) → 3.5
            if m.get("fast") in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-latest", "gemini-1.5-flash-001"):
                m["fast"] = "gemini-3.5-flash-lite"
            if m.get("pro") in ("gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-pro-latest", "gemini-1.5-flash", "gemini-1.5-flash-latest"):
                m["pro"] = "gemini-3.5-flash"
            if "2.5" in m.get("fast","") or "2.0" in m.get("fast","") or "1.5" in m.get("fast",""):
                m["fast"] = DEFAULT_PROVIDERS["gemini"]["models"]["fast"]
            if "2.5" in m.get("pro","") or "2.0" in m.get("pro","") or "1.5" in m.get("pro",""):
                m["pro"] = DEFAULT_PROVIDERS["gemini"]["models"]["pro"]
        # ENV перекрывает cache — ключи живут в .env (гитигнор)
        saved = _apply_env_overrides(saved)
        # сохраняем миграцию
        try:
            save_providers(saved)
        except Exception:
            pass
        return saved

    # First run — try loading Gemini key from api.txt + ENV
    providers = dict(DEFAULT_PROVIDERS)
    providers = _apply_env_overrides(providers)
    api_txt = os.path.join(BASE_DIR, "api.txt")
    if os.path.exists(api_txt):
        with open(api_txt, "r") as f:
            key = f.read().strip()
        if key and not providers["gemini"]["api_key"]:
            providers["gemini"]["api_key"] = key
            providers["gemini"]["enabled"] = True

    save_providers(providers)
    return providers


def save_providers(providers: dict):
    """Save provider configs to cache."""
    with open(PROVIDERS_PATH, "w", encoding="utf-8") as f:
        json.dump(providers, f, ensure_ascii=False, indent=2)


def get_active_provider() -> tuple[str, dict]:
    """Get the first enabled provider with a key. Muse Spark приоритет — чат строго через него."""
    providers = load_providers()
    # Чат и агент — строго muse_spark если он enabled
    if "muse_spark" in providers and providers["muse_spark"].get("enabled"):
        cfg = providers["muse_spark"]
        if cfg.get("api_key"):
            return "muse_spark", cfg
        # muse_spark enabled но без ключа → честная ошибка, не fallback на gemini для чата
        raise ValueError("Muse Spark enabled but API key is empty. Add Muse Spark key in Providers (Gemini — только Vision).")
    for pid, cfg in providers.items():
        if cfg.get("enabled") and cfg.get("api_key"):
            return pid, cfg
    raise ValueError("No active provider configured! Add an API key in the admin panel.")


def get_vision_provider() -> tuple[str, dict] | None:
    """Best provider for PDF Vision (native PDF input). Prefers Gemini even if disabled."""
    providers = load_providers()
    # Prefer Gemini with a key, even if not enabled (fallback for Vision only)
    gem = providers.get("gemini")
    if gem and gem.get("api_key"):
        return "gemini", gem
    # Otherwise try active provider if it supports vision
    try:
        pid, cfg = get_active_provider()
        if cfg.get("type") == "gemini":
            return pid, cfg
    except Exception:
        pass
    return None


# Scenarios that historically need extra verification / Vision re-extraction
PROBLEMATIC_SCENARIOS = ["P3", "P4", "P5", "P6", "P9"]  # from honest 85.56% run


# === Borrower mapping ===
ACCOUNT_TO_SCENARIO = {
    "ACC-7201": "B1", "ACC-7204": "B4",
    "ACC-7801": "P1", "ACC-7802": "P2", "ACC-7803": "P3",
    "ACC-7804": "P4", "ACC-7805": "P5", "ACC-7806": "P6",
    "ACC-7807": "P7", "ACC-7808": "P8", "ACC-7809": "P9",
    "ACC-7810": "P10",
}
SCENARIO_TO_ACCOUNT = {v: k for k, v in ACCOUNT_TO_SCENARIO.items()}
SCENARIOS = sorted(ACCOUNT_TO_SCENARIO.values())
COVENANTS = ["6.1", "6.2", "6.3"]
