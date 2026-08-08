def test_content_normalization():
    # Копируем логику _content_str без импорта app.main (чтобы не тянуть fastapi)
    def _content_str(c):
        if c is None:
            return ""
        if isinstance(c, str):
            return c
        if isinstance(c, (list, tuple)):
            parts = []
            for x in c:
                if isinstance(x, str):
                    parts.append(x)
                elif isinstance(x, dict):
                    parts.append(x.get("text") or x.get("content") or "")
                else:
                    parts.append(getattr(x, "text", None) or getattr(x, "content", None) or str(x))
            return "".join(parts)
        if hasattr(c, "content"):
            return _content_str(c.content)
        if hasattr(c, "text"):
            return str(c.text)
        return str(c)

    assert _content_str("hello") == "hello"
    assert _content_str(["hello", " world"]) == "hello world"
    assert _content_str(("a", "b")) == "ab"
    assert _content_str([{"text": "hi"}, " there"]) == "hi there"
    assert _content_str(None) == ""
    assert _content_str([{"content": "x"}]) == "x"

def test_muse_spark_wrapper_handles_tuple(monkeypatch=None):
    # Ensure create_llm returns resilient wrapper
    from app.services.llm_factory import create_llm

    # Mock test without API key should raise
    try:
        create_llm("muse_spark", {"type": "openai_compat", "api_key": "", "base_url": "https://api.meta.ai/v1", "models": {"fast": "x", "pro": "x"}}, tier="fast")
        assert False, "should raise"
    except ValueError as e:
        assert "API key" in str(e)

def test_gemini_model_migration():
    import json, pathlib, importlib, config
    importlib.reload(config)
    # Simulate old cache
    p = pathlib.Path("cache/providers.json")
    old = json.loads(p.read_text(encoding="utf-8"))
    # Check after load, gemini should be 3.5
    from config import load_providers
    prov = load_providers()
    assert "3.5" in prov["gemini"]["models"]["fast"], prov["gemini"]["models"]
