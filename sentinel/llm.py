"""Unified LLM caller - tries enabled providers in order with automatic fallback."""
import logging
import time
from sentinel import config

log = logging.getLogger("sentinel.llm")

# Lazy-loaded clients
_clients = {}


def _get_gemini_client(api_key: str):
    """Get or create a Gemini client."""
    cache_key = f"gemini:{api_key[:8]}"
    if cache_key not in _clients:
        import google.genai as genai
        _clients[cache_key] = genai.Client(api_key=api_key)
    return _clients[cache_key]


def _get_openai_client(api_key: str, base_url: str):
    """Get or create an OpenAI-compatible client."""
    cache_key = f"openai:{base_url}:{api_key[:8]}"
    if cache_key not in _clients:
        from openai import OpenAI
        _clients[cache_key] = OpenAI(api_key=api_key, base_url=base_url)
    return _clients[cache_key]


def _get_anthropic_client(api_key: str):
    """Get or create an Anthropic client."""
    cache_key = f"anthropic:{api_key[:8]}"
    if cache_key not in _clients:
        import anthropic
        _clients[cache_key] = anthropic.Anthropic(api_key=api_key)
    return _clients[cache_key]


def _is_rate_error(error_str: str) -> bool:
    keywords = ["429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "quota",
                "rate_limit", "overloaded", "Too Many Requests"]
    return any(k.lower() in error_str.lower() for k in keywords)


def _record_rate_error(provider_name: str, model: str, error_str: str) -> None:
    """Defensive wrapper around llm_health.record_rate_error.

    Health logging must never break the real call path's exception
    handling. We lazy-import inside a try/except so a bad llm_health
    edit can't take the LLM caller down with it.
    """
    try:
        from sentinel.llm_health import record_rate_error
        record_rate_error(provider_name, model, error_str)
    except Exception as e:
        log.debug(f"llm_health record failed: {e}")


def _call_gemini(provider: dict, prompt: str, system: str = "",
                 temperature: float = 0.5, max_tokens: int = 800) -> str | None:
    """Call Gemini API."""
    import google.genai as genai
    client = _get_gemini_client(provider["api_key"])

    for model in provider["models"]:
        try:
            kwargs = {"temperature": temperature, "max_output_tokens": max_tokens}
            if system:
                kwargs["system_instruction"] = system
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(**kwargs),
            )
            return response.text.strip()
        except Exception as e:
            err = str(e)
            log.warning(f"Gemini/{model} failed: {err}")
            if _is_rate_error(err):
                _record_rate_error("Gemini", model, err)
            continue  # 任何錯誤都繼續嘗試下一個 model
    return None


def _call_openai_compat(provider: dict, prompt: str, system: str = "",
                        temperature: float = 0.5, max_tokens: int = 800) -> str | None:
    """Call OpenAI-compatible API (OpenRouter, Groq, DeepSeek, etc.)."""
    client = _get_openai_client(provider["api_key"], provider["base_url"])

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for model in provider["models"]:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            log.warning(f"{provider['name']}/{model} failed: {err}")
            if _is_rate_error(err):
                _record_rate_error(provider["name"], model, err)
            continue  # 任何錯誤都繼續嘗試下一個 model
    return None


def _call_anthropic(provider: dict, prompt: str, system: str = "",
                    temperature: float = 0.5, max_tokens: int = 800) -> str | None:
    """Call Anthropic API."""
    client = _get_anthropic_client(provider["api_key"])

    for model in provider["models"]:
        try:
            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            response = client.messages.create(**kwargs)
            return response.content[0].text.strip()
        except Exception as e:
            err = str(e)
            log.warning(f"Anthropic/{model} failed: {err}")
            if _is_rate_error(err):
                _record_rate_error("Anthropic", model, err)
            continue  # 任何錯誤都繼續嘗試下一個 model
    return None


_CALLERS = {
    "gemini": _call_gemini,
    "openai_compat": _call_openai_compat,
    "anthropic": _call_anthropic,
}


def _try_local(prompt: str, system: str, temperature: float,
               max_tokens: int) -> str | None:
    """Try calling a local Ollama model."""
    try:
        from sentinel.local_llm import is_ollama_running, call_local, get_best_local_model
        if is_ollama_running():
            model = get_best_local_model()
            if model:
                full_prompt = f"{system}\n\n{prompt}" if system else prompt
                result = call_local(full_prompt, model=model,
                                    temperature=temperature, max_tokens=max_tokens)
                if result:
                    log.debug(f"Got response from local model: {model}")
                    return result
    except Exception as e:
        log.debug(f"Local LLM not available: {e}")
    return None


def _try_cloud(prompt: str, system: str, temperature: float,
               max_tokens: int) -> str | None:
    """Try all enabled cloud providers in order."""
    tried = []
    for provider in config.LLM_PROVIDERS:
        name = provider.get("name", "?")
        if not provider.get("enabled"):
            continue
        if not provider.get("api_key"):
            log.debug(f"{name}: 跳過（無 API key）")
            continue

        caller = _CALLERS.get(provider["type"])
        if not caller:
            log.warning(f"{name}: 跳過（未知 provider type: {provider['type']}）")
            continue

        tried.append(name)
        try:
            result = caller(provider, prompt, system, temperature, max_tokens)
            if result:
                log.debug(f"Got response from {name}")
                return result
            log.info(f"{name}: 所有 model 都無回應，嘗試下一個 provider...")
        except Exception as e:
            log.warning(f"{name} 異常: {e}，嘗試下一個 provider...")
            continue

    if tried:
        log.warning(f"雲端 providers 全部失敗: {', '.join(tried)}")
    else:
        log.warning("沒有任何已啟用且有 API key 的雲端 provider")
    return None


def _try_relay(prompt: str, system: str, temperature: float,
               max_tokens: int, task_type: str) -> str | None:
    """Try calling LLM through relay server (paid quota mode)."""
    try:
        from sentinel.wallet.quota import QuotaManager
        qm = get_quota_manager()
        if qm and qm.mode == "quota" and qm.is_logged_in:
            result = qm.relay_llm_call(
                prompt, task_type=task_type, system=system,
                temperature=temperature, max_tokens=max_tokens,
            )
            if result:
                log.debug(f"Got response from relay (quota mode)")
                return result
    except Exception as e:
        log.debug(f"Relay call failed: {e}")
    return None


# Singleton quota manager (lazy init)
_quota_manager = None


def get_quota_manager():
    """Get the global QuotaManager instance (or None if not configured)."""
    global _quota_manager
    if _quota_manager is None:
        try:
            from sentinel.wallet.quota import QuotaManager
            relay_url = getattr(config, "RELAY_SERVER_URL", "")
            _quota_manager = QuotaManager(relay_url=relay_url)
        except Exception:
            return None
    return _quota_manager


def call_llm(prompt: str, system: str = "",
             temperature: float = 0.5, max_tokens: int = 800,
             prefer_local: bool = False,
             model_pref: str | None = None,
             task_type: str = "chat") -> str | None:
    """Try providers based on preference and user mode.

    Routing logic:
      1. If user is in QUOTA mode → relay server first (points deducted)
      2. Otherwise → local/cloud based on model_pref
      3. 不管偏好，全部失敗後一定嘗試另一個路徑（雲端失敗→本地，本地失敗→雲端）
    """
    # QUOTA mode: relay server handles LLM + billing
    qm = get_quota_manager()
    if qm and qm.mode == "quota" and qm.is_logged_in:
        result = _try_relay(prompt, system, temperature, max_tokens, task_type)
        if result:
            return result
        log.warning("Relay failed, falling back to local/BYOK providers")

    # BYOK mode (or relay fallback)
    if model_pref is None:
        pref = "local_first" if prefer_local else "cloud_first"
    else:
        pref = model_pref

    if pref == "local_only":
        result = _try_local(prompt, system, temperature, max_tokens)
        if not result:
            log.warning("Local-only mode: no local model available")
        return result

    if pref == "local_first":
        result = _try_local(prompt, system, temperature, max_tokens)
        if result:
            return result
        log.info("Local LLM 無回應，fallback 到雲端 providers...")
        result = _try_cloud(prompt, system, temperature, max_tokens)
        if result:
            return result
        log.warning("所有 LLM（本地+雲端）都無回應")
        return None

    # cloud_first (default)
    result = _try_cloud(prompt, system, temperature, max_tokens)
    if result:
        return result
    log.info("雲端 providers 全部失敗，fallback 到本地 Ollama...")
    result = _try_local(prompt, system, temperature, max_tokens)
    if result:
        return result
    log.warning("所有 LLM（雲端+本地）都無回應")
    return None
