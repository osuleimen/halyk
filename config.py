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
    "gemini": {
        "name": "Google Gemini",
        "type": "gemini",              # native google-genai — fallback only
        "api_key": "",
        "models": {
            "fast": "gemini-2.5-flash",
            "pro": "gemini-2.5-pro",
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


def load_providers() -> dict:
    """Load provider configs from cache or defaults."""
    if os.path.exists(PROVIDERS_PATH):
        with open(PROVIDERS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # Merge with defaults (new providers added in code)
        for k, v in DEFAULT_PROVIDERS.items():
            if k not in saved:
                saved[k] = v
        return saved

    # First run — try loading Gemini key from api.txt
    providers = dict(DEFAULT_PROVIDERS)
    api_txt = os.path.join(BASE_DIR, "api.txt")
    if os.path.exists(api_txt):
        with open(api_txt, "r") as f:
            key = f.read().strip()
        if key:
            providers["gemini"]["api_key"] = key
            providers["gemini"]["enabled"] = True

    save_providers(providers)
    return providers


def save_providers(providers: dict):
    """Save provider configs to cache."""
    with open(PROVIDERS_PATH, "w", encoding="utf-8") as f:
        json.dump(providers, f, ensure_ascii=False, indent=2)


def get_active_provider() -> tuple[str, dict]:
    """Get the first enabled provider with a key. Returns (provider_id, config)."""
    providers = load_providers()
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
