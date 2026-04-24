"""Vision pipeline — send screenshots to a vision-capable LLM.

Builds on the screenshot primitive from Phase C2 (`surface.take_
screenshot`) and the multi-provider LLM setup in `sentinel.llm`. The
existing 千里眼 feature (screen_watcher.py) already carried a VLM
implementation, but it was pinned to its own prompt and embedded
inside the watcher. Phase D3 factors the VLM call out into a reusable
primitive so action handlers, future VLM-backed workflows, and
anything else can interpret arbitrary screenshots with arbitrary
prompts.

Why this module exists separately from screen_watcher.py
--------------------------------------------------------
screen_watcher owns *scheduling* (when to auto-capture) and *privacy
redaction* (drop images with obvious secrets). vision.py owns the
*call* to the VLM — nothing about when or whether to take the
screenshot. The two talk to each other through the Surface primitive
and the Context Bus.

Public API
----------
    interpret(image_path, prompt) -> dict
        Send an image + prompt to the first available VLM provider.
        Returns {"ok": bool, "analysis": str, "provider": str,
        "model": str, "error": str}.

    interpret_current_screen(prompt) -> dict
        Convenience: take a screenshot via surface, pass it to
        interpret(), clean up the file. Used by the vision action
        handler.

Safety
------
Screenshots contain everything on the user's display — passwords,
private chats, secrets. This module will happily send the whole
thing to a cloud provider. The approval layer (Phase C1) sits
between the slime proposing a vision call and this module actually
firing, so no frame leaves the machine without the user clicking
同意.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Optional

from sentinel import config

log = logging.getLogger("sentinel.vision")


# Default prompt for "just describe the screen" — used when the caller
# doesn't specify something more targeted. Kept short because VLM
# tokens are the expensive part of these calls.
DEFAULT_PROMPT = (
    "你是 AI Slime 的千里眼。描述這張螢幕截圖裡你看到的主要內容："
    "應用程式、正在做的事、任何錯誤訊息或警示、主人的大致狀態。"
    "50 字內，中文，不要瞎猜你看不清的東西。"
)

# Max analysis length so a chatty VLM doesn't eat the whole context
# window when slime quotes it back in chat.
MAX_ANALYSIS_TOKENS = 500


def _rate_limit_hint(error_str: str) -> bool:
    """Detect transient / rate-limit errors so the fallback loop can
    try another provider instead of propagating a known-retryable."""
    markers = ("429", "503", "rate", "quota", "exhausted",
               "overloaded", "unavailable")
    e = error_str.lower()
    return any(m in e for m in markers)


# ── Provider-specific calls ───────────────────────────────────────


def _call_gemini(provider: dict, image_path: str, prompt: str) -> Optional[str]:
    """Google Gemini vision (text-only or multimodal models)."""
    try:
        import google.genai as genai
        from google.genai import types
    except ImportError:
        log.warning("google-genai not installed; skipping Gemini vision")
        return None

    client = genai.Client(api_key=provider["api_key"])
    try:
        data = Path(image_path).read_bytes()
    except OSError as e:
        log.error(f"read screenshot failed: {e}")
        return None
    image_part = types.Part.from_bytes(data=data, mime_type="image/png")

    for model in provider.get("models", []):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[image_part, prompt],
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=MAX_ANALYSIS_TOKENS,
                ),
            )
            text = (response.text or "").strip()
            if text:
                return text
        except Exception as e:
            if _rate_limit_hint(str(e)):
                log.info(f"Gemini/{model} rate-limited, trying next model")
                continue
            log.warning(f"Gemini/{model} vision error: {e}")
            continue
    return None


def _call_anthropic(provider: dict, image_path: str, prompt: str) -> Optional[str]:
    """Anthropic Claude vision."""
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic not installed; skipping Claude vision")
        return None

    client = anthropic.Anthropic(api_key=provider["api_key"])
    try:
        raw = Path(image_path).read_bytes()
        data = base64.standard_b64encode(raw).decode("utf-8")
    except OSError as e:
        log.error(f"read screenshot failed: {e}")
        return None

    for model in provider.get("models", []):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=MAX_ANALYSIS_TOKENS,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": data,
                        }},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            if response.content:
                return response.content[0].text.strip()
        except Exception as e:
            if _rate_limit_hint(str(e)):
                log.info(f"Anthropic/{model} rate-limited, trying next model")
                continue
            log.warning(f"Anthropic/{model} vision error: {e}")
            continue
    return None


def _call_openai(provider: dict, image_path: str, prompt: str) -> Optional[str]:
    """OpenAI-compatible vision (gpt-4o, gpt-4-turbo).

    OpenAI's multimodal schema differs from Gemini/Anthropic — image
    goes as an `image_url` part with a data: URI. Many OpenAI-
    compatible providers (OpenRouter, Groq, etc.) follow the same
    shape so this function tends to "just work" past Gemini/Anthropic.
    """
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai not installed; skipping OpenAI vision")
        return None

    try:
        raw = Path(image_path).read_bytes()
        b64 = base64.standard_b64encode(raw).decode("utf-8")
    except OSError as e:
        log.error(f"read screenshot failed: {e}")
        return None
    data_uri = f"data:image/png;base64,{b64}"

    client = OpenAI(
        api_key=provider["api_key"],
        base_url=provider.get("base_url", "https://api.openai.com/v1"),
    )
    for model in provider.get("models", []):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=MAX_ANALYSIS_TOKENS,
                temperature=0.3,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }],
            )
            if response.choices:
                text = (response.choices[0].message.content or "").strip()
                if text:
                    return text
        except Exception as e:
            if _rate_limit_hint(str(e)):
                log.info(f"OpenAI/{model} rate-limited, trying next model")
                continue
            log.warning(f"OpenAI/{model} vision error: {e}")
            continue
    return None


# ── Public API ────────────────────────────────────────────────────


def interpret(image_path: str, prompt: Optional[str] = None) -> dict:
    """Run the VLM pipeline with fallback across configured providers.

    Returns:
      On success: {"ok": True, "analysis": str, "provider": str,
                   "model": str (best-effort, may be empty)}
      On failure: {"ok": False, "error": str}

    The caller gets back structured info so chat handlers can say
    「Gemini 的 flash 看完你的螢幕說 ...」 with attribution, and the
    audit log captures which provider saw the screenshot.
    """
    if not image_path or not os.path.exists(image_path):
        return {"ok": False, "error": "image not found"}
    prompt = (prompt or DEFAULT_PROMPT).strip()
    if not prompt:
        return {"ok": False, "error": "empty prompt"}

    providers = getattr(config, "LLM_PROVIDERS", None) or []
    attempted: list[str] = []

    for provider in providers:
        if not provider.get("enabled") or not provider.get("api_key"):
            continue
        ptype = provider.get("type")
        name = provider.get("name", ptype or "?")
        attempted.append(name)

        result: Optional[str] = None
        if ptype == "gemini":
            result = _call_gemini(provider, image_path, prompt)
        elif ptype == "anthropic":
            result = _call_anthropic(provider, image_path, prompt)
        elif ptype in ("openai", "openai_compat"):
            result = _call_openai(provider, image_path, prompt)
        else:
            continue

        if result:
            return {
                "ok": True,
                "analysis": result,
                "provider": name,
                "model": "",  # we don't track which model-iteration won
            }

    return {
        "ok": False,
        "error": f"no vision-capable provider succeeded; attempted: {attempted}",
    }


def interpret_current_screen(prompt: Optional[str] = None,
                             cleanup: bool = True) -> dict:
    """Take a screenshot via the Surface primitive and interpret it.

    Convenience wrapper so callers don't have to orchestrate capture +
    analyse + delete themselves. `cleanup=True` (default) removes the
    screenshot file after analysis — screen content is private by
    default; the only time a caller would want cleanup=False is
    debugging.

    Returns the same dict shape as interpret(), plus:
      "screenshot_path": str (the path, absent if cleaned up)
    """
    from sentinel.surface import get_surface

    surface = get_surface()
    cap = surface.take_screenshot()
    if not cap.get("ok"):
        return {"ok": False, "error": f"screenshot failed: {cap.get('error')}"}

    image_path = cap["path"]
    try:
        result = interpret(image_path, prompt)
    finally:
        if cleanup:
            try:
                Path(image_path).unlink(missing_ok=True)
            except OSError as e:
                log.warning(f"screenshot cleanup failed: {e}")

    if not cleanup and result.get("ok"):
        result["screenshot_path"] = image_path
    return result
