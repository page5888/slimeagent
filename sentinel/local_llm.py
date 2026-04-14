"""Local LLM support via Ollama.

AI Slime's brain can run locally for frequent tasks (analysis, distillation),
saving cloud API for heavy tasks (vision, code generation).

Setup: Install Ollama (https://ollama.com), then:
  ollama pull qwen3:4b

That's it. AI Slime will auto-detect and use local model when available.
"""
import logging
import json

log = logging.getLogger("rimuru.local_llm")

OLLAMA_URL = "http://localhost:11434"
_ollama_available = None  # Cache availability check


import time as _time
_ollama_cache_ts = 0  # 快取的時間戳


def is_ollama_running() -> bool:
    """Check if Ollama is running locally. 快取 60 秒，避免每次都連線但也不會永久卡住。"""
    global _ollama_available, _ollama_cache_ts
    now = _time.time()
    # 快取 60 秒後自動重新偵測
    if _ollama_available is not None and now - _ollama_cache_ts < 60:
        return _ollama_available

    try:
        import urllib.request
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            _ollama_available = resp.status == 200
            _ollama_cache_ts = now
            return _ollama_available
    except Exception:
        _ollama_available = False
        _ollama_cache_ts = now
        return False


def reset_availability_cache():
    """Reset the cache so next call re-checks Ollama."""
    global _ollama_available
    _ollama_available = None


def list_local_models() -> list[str]:
    """List available Ollama models."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def call_local(prompt: str, model: str = "qwen3:4b",
               temperature: float = 0.3, max_tokens: int = 500) -> str | None:
    """Call local Ollama model.

    Used for high-frequency, low-complexity tasks:
    - Should I notify? (brain analysis)
    - Extract patterns from activity (distillation)
    - Simple chat responses
    """
    if not is_ollama_running():
        return None

    try:
        import urllib.request

        payload = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            text = result.get("response", "").strip()
            if text:
                log.debug(f"Local LLM ({model}): {text[:80]}...")
                return text
            return None

    except Exception as e:
        log.warning(f"Local LLM error: {e}")
        # Mark as unavailable so we don't keep trying
        reset_availability_cache()
        return None


# Preferred models for AI Slime (in order of preference)
PREFERRED_MODELS = [
    "qwen3:4b",
    "qwen2.5:3b",
    "phi4-mini:latest",
    "gemma3:4b",
    "llama3.2:3b",
]


def get_best_local_model() -> str | None:
    """Find the best available local model."""
    available = list_local_models()
    if not available:
        return None

    for preferred in PREFERRED_MODELS:
        for avail in available:
            if preferred.split(":")[0] in avail:
                return avail

    # Fallback: use whatever is available
    return available[0] if available else None
