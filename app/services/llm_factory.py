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
        # Gemini Vision — Lite основная, Flash fallback (по просьбе: только 3.5)
        candidates = [model_name, "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-1.5-flash-latest", "gemini-pro"]
        last_err = None
        for cand in candidates:
            try:
                llm = ChatGoogleGenerativeAI(
                    model=cand,
                    google_api_key=api_key,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                )
                # быстрый тест что модель резолвится (не вызываем, только создаём)
                logger.info("Created Gemini LLM: %s (requested %s)", cand, model_name)
                return llm
            except Exception as e:
                last_err = e
                if "404" in str(e) or "NOT_FOUND" in str(e):
                    logger.warning("Gemini model %s not found, trying fallback", cand)
                    continue
                raise
        raise last_err if last_err else ValueError(f"Gemini model {model_name} not found")

    elif ptype == "openai_compat":
        from langchain_openai import ChatOpenAI
        import time

        base_url = provider_config.get("base_url", "https://api.openai.com/v1")

        class _ResilientChatOpenAI(ChatOpenAI):
            """BigTech: muse-spark иногда отдаёт content как tuple/list + 404/429 — ретраи, нормализация, fallback."""

            def _normalize_content(self, resp):
                """Нормализуем content к str, независимо от API (str|list|tuple|dict)."""
                try:
                    c = getattr(resp, "content", None)
                    if c is None:
                        return resp
                    if isinstance(c, str):
                        return resp
                    if isinstance(c, (list, tuple)):
                        parts = []
                        for x in c:
                            if isinstance(x, str):
                                parts.append(x)
                            elif isinstance(x, dict):
                                parts.append(x.get("text") or x.get("content") or "")
                            else:
                                # AIMessage chunk
                                txt = getattr(x, "text", None) or getattr(x, "content", None)
                                if txt is None and isinstance(x, dict):
                                    txt = x.get("text", "")
                                parts.append(str(txt) if txt is not None else "")
                        resp.content = "".join(parts) if parts else ""
                    elif hasattr(c, "content"):
                        resp.content = self._normalize_content(c).content if hasattr(c, "content") else str(c)
                except Exception as e:
                    logger.warning("Content normalization failed: %s", e)
                return resp

            def invoke(self, *args, **kwargs):
                last_err = None
                for attempt in range(2):
                    try:
                        resp = super().invoke(*args, **kwargs)
                        return self._normalize_content(resp)
                    except Exception as e:
                        msg = str(e).lower()
                        last_err = e
                        if "404" in msg or "not_found" in msg or "not found" in msg:
                            logger.warning("OpenAI-compat 404 on %s (attempt %d): %s", model_name, attempt, e)
                            if attempt == 0:
                                kwargs.pop("tools", None)
                                kwargs.pop("tool_choice", None)
                                time.sleep(0.5)
                                continue
                        if "tuple" in msg and "index" in msg:
                            logger.warning("OpenAI-compat tuple error attempt %d: %s", attempt, e)
                            kwargs.pop("tools", None)
                            kwargs.pop("tool_choice", None)
                            if attempt == 0:
                                time.sleep(0.5)
                                continue
                        raise
                raise last_err if last_err else RuntimeError("LLM invoke failed")

            async def ainvoke(self, *args, **kwargs):
                return self.invoke(*args, **kwargs)

            def stream(self, *args, **kwargs):
                # BigTech: stream тоже может отдать tuple — нормализуем
                try:
                    for chunk in super().stream(*args, **kwargs):
                        yield self._normalize_content(chunk)
                except Exception as e:
                    if "tuple" in str(e).lower():
                        logger.warning("Stream tuple error, fallback to invoke: %s", e)
                        yield self.invoke(*args, **kwargs)
                    else:
                        raise

            async def astream(self, *args, **kwargs):
                async for chunk in super().astream(*args, **kwargs):
                    yield self._normalize_content(chunk)

            def batch(self, *args, **kwargs):
                # batch может вернуть list[BaseMessage] с tuple content
                res = super().batch(*args, **kwargs)
                return [self._normalize_content(r) for r in res]

            async def abatch(self, *args, **kwargs):
                res = await super().abatch(*args, **kwargs)
                return [self._normalize_content(r) for r in res]

        llm = _ResilientChatOpenAI(
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
        raw = response.content if hasattr(response, "content") else response
        if isinstance(raw, list):
            raw = "".join([x if isinstance(x, str) else x.get("text","") if isinstance(x, dict) else str(getattr(x,"text",x)) for x in raw])
        content = (raw[:100] if raw else "empty response")
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
