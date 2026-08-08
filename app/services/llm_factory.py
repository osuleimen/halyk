"""
LLM Factory — creates LangChain LLM instances from provider config.
Supports Gemini (native), DeepSeek, Muse Spark, OpenAI, Groq (OpenAI-compat).
"""
from __future__ import annotations
import re
import logging
from typing import Literal

logger = logging.getLogger(__name__)


def _clean_api_key(key: str) -> str:
    """Clean API key — remove pasted page text, whitespace, etc."""
    if not key:
        return key
    key = key.strip()
    # If user pasted the whole Gemini page, extract the key
    # Gemini keys look like: AIza... or AQ.Ab8...
    if "\n" in key or len(key) > 200:
        # Try to find a Gemini-style key
        match = re.search(r'(AI[a-zA-Z0-9_-]{30,}|AQ\.[A-Za-z0-9_-]{30,})', key)
        if match:
            return match.group(1)
        # Try to find an sk- style key (OpenAI/DeepSeek/Anthropic)
        match = re.search(r'(sk-[a-zA-Z0-9-]{20,})', key)
        if match:
            return match.group(1)
        # Try to find LLM_ style key (Meta)
        match = re.search(r'(LLM_[a-zA-Z0-9_-]{20,})', key)
        if match:
            return match.group(1)
    return key


def create_llm(
    provider_id: str,
    provider_config: dict,
    tier: Literal["fast", "pro"] = "fast",
    temperature: float = 0.0,
    max_tokens: int = 16384,
):
    """Create a LangChain ChatModel from provider config."""
    ptype = provider_config.get("type", "openai_compat")
    api_key = _clean_api_key(provider_config["api_key"])
    model_name = provider_config["models"][tier]

    if not api_key:
        raise ValueError(f"No API key configured for {provider_id}")

    if ptype == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        logger.info("Created Gemini LLM: %s", model_name)
        return llm

    elif ptype == "openai_compat":
        from langchain_openai import ChatOpenAI
        base_url = provider_config.get("base_url", "https://api.openai.com/v1")
        llm = ChatOpenAI(
            model=model_name,
            openai_api_key=api_key,
            openai_api_base=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        logger.info("Created OpenAI-compat LLM: %s @ %s", model_name, base_url)
        return llm
        
    elif ptype == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("langchain-anthropic is not installed. Run: pip install langchain-anthropic")
            
        llm = ChatAnthropic(
            model=model_name,
            anthropic_api_key=api_key,
            temperature=temperature,
            max_tokens_to_sample=max_tokens,
        )
        logger.info("Created Anthropic LLM: %s", model_name)
        return llm

    else:
        raise ValueError(f"Unknown provider type: {ptype}")


def create_vision_llm(provider_id: str, provider_config: dict):
    """Create an LLM suitable for vision/PDF tasks.
    Only Gemini supports native PDF input.
    """
    if provider_config.get("type") == "gemini":
        from google import genai
        api_key = _clean_api_key(provider_config["api_key"])
        if not api_key:
            return None
        client = genai.Client(api_key=api_key)
        return client
    return None


def get_vision_client():
    """Resolve best Vision client using config.get_vision_provider (Gemini fallback)."""
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from config import get_vision_provider
        vp = get_vision_provider()
        if vp is None:
            return None, None, None
        pid, cfg = vp
        client = create_vision_llm(pid, cfg)
        return client, pid, cfg
    except Exception as e:
        logger.warning("Vision client resolve failed: %s", e)
        return None, None, None


def test_provider(provider_id: str, provider_config: dict) -> dict:
    """Test if a provider is working. Returns {ok: bool, message: str}."""
    try:
        # Clean the key first
        cfg_copy = dict(provider_config)
        cfg_copy["api_key"] = _clean_api_key(cfg_copy.get("api_key", ""))
        
        if not cfg_copy["api_key"]:
            return {"ok": False, "message": "API ключ пуст. Введите ключ и нажмите 'Сохранить'."}
        
        llm = create_llm(provider_id, cfg_copy, tier="fast", max_tokens=50)
        response = llm.invoke("Say 'OK' in one word")
        content = response.content[:100] if response.content else "empty response"
        return {"ok": True, "message": f"✅ Работает! Ответ: {content}"}
    except Exception as e:
        err = str(e)
        # Better error messages
        if "401" in err or "Unauthorized" in err:
            return {"ok": False, "message": "❌ 401 Unauthorized — неверный API ключ. Проверьте ключ."}
        elif "404" in err or "Not Found" in err:
            return {"ok": False, "message": "❌ 404 — модель или endpoint не найден. Проверьте base_url и модель."}
        elif "429" in err or "RESOURCE_EXHAUSTED" in err:
            return {"ok": False, "message": "⚠️ 429 Rate Limit — ключ верный, но квота исчерпана. Подождите."}
        elif "400" in err or "Bad Request" in err:
            return {"ok": False, "message": f"❌ 400 Bad Request — {err[:150]}"}
        else:
            return {"ok": False, "message": f"❌ Ошибка: {err[:200]}"}
