"""Generator — orchestrates the full pipeline.

Pipeline per expression:
  1. Pick kind (random weighted, or caller-specified)
  2. Slime writes its own prompt + caption (via prompts.py)
  3. Image API call with multi-key + multi-provider fallback
  4. Persist binary + metadata
  5. Return Expression object for the container layer to render

Image fallback ladder:
  For each enabled, image-capable provider in LLM_PROVIDERS order,
  for each API key on that provider (comma/newline-separated in the
  `api_key` field), try the provider's image backend. A 429 / quota
  error on a key skips to the next key; an empty provider skips to
  the next provider. First success wins.

  Image-capable providers in v0.7-alpha:
    - Gemini (type=gemini): gemini-2.5-flash-image cascade
    - OpenAI (type=openai_compat with api.openai.com base_url): DALL-E 3

  Other openai_compat providers (OpenRouter, Groq, DeepSeek) are
  text-only and skipped silently. If you want OpenRouter image gen,
  add it explicitly with a different type.

Cost model (for v0.7-alpha dogfood — single user, no monetization):
  - Gemini free tier covers gemini-2.5-flash-image (the primary path)
  - OpenAI fallback is paid — only triggered when every Gemini key
    is exhausted, so cost stays bounded
  - max one auto-generation per week (Sunday evening trigger)
  - manual "請畫一張" allowed but cooldown-gated to prevent spam

No Qt imports. No GUI imports.
"""
from __future__ import annotations

import logging
import random
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from sentinel.expression.album import (
    EXPRESSIONS_DIR,
    Expression,
    ExpressionKind,
    new_id,
    save_expression,
)
from sentinel.expression import prompts

log = logging.getLogger("sentinel.expression.generator")


# ── API integration ────────────────────────────────────────────────


def _iter_api_keys(provider: dict) -> list[str]:
    """Extract one or more keys from a provider's `api_key` field.

    Accepts either a list (already split) or a comma/newline-separated
    string — the latter lets users paste several keys into the existing
    single-line GUI field without schema changes. Empty entries dropped.
    """
    raw = provider.get("api_key")
    if isinstance(raw, list):
        return [k.strip() for k in raw if isinstance(k, str) and k.strip()]
    if isinstance(raw, str):
        parts = re.split(r"[,\n]", raw)
        return [p.strip() for p in parts if p.strip()]
    return []


def _is_quota_error(err_str: str) -> bool:
    """A key-level quota / rate signal — means we should stop trying
    *this* key (further models or retries on the same key won't help)
    and move on to the next key or provider."""
    s = err_str.lower()
    return any(tok in s for tok in (
        "429", "resource_exhausted", "quota", "rate_limit",
        "rate limit", "too many requests", "insufficient_quota",
    ))


def _call_gemini_image(api_key: str, prompt_text: str, image_path: Path
                       ) -> tuple[bool, str]:
    """Single-key Gemini image attempt. Returns (success, model_name).

    Cascades through image-capable Gemini models: gemini-2.5-flash-image
    (free-tier sweet spot), then preview, then paid imagen-4.0. On a
    quota error (429 / RESOURCE_EXHAUSTED) we bail early — quotas are
    per-key, so further models on the same key would only burn more
    failed requests.
    """
    try:
        from google import genai as _genai
        from google.genai import types as _types
    except ImportError:
        log.warning(
            "expression: `google.genai` not installed — "
            "run `pip install google-genai` in venv"
        )
        return False, ""

    candidates = [
        ("gemini-2.5-flash-image",          "generate_content"),
        ("gemini-3.1-flash-image-preview",  "generate_content"),
        ("imagen-4.0-fast-generate-001",    "generate_images"),  # paid
    ]

    client = _genai.Client(api_key=api_key)

    for model_name, method in candidates:
        try:
            if method == "generate_content":
                result = client.models.generate_content(
                    model=model_name,
                    contents=prompt_text,
                    config=_types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                )
                for cand in (result.candidates or []):
                    for part in (cand.content.parts or []):
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            image_path.write_bytes(inline.data)
                            log.info("expression: image saved via %s", model_name)
                            return True, model_name

            elif method == "generate_images":
                result = client.models.generate_images(
                    model=model_name,
                    prompt=prompt_text,
                    config={"number_of_images": 1, "aspect_ratio": "1:1"},
                )
                if result.generated_images:
                    img_obj = result.generated_images[0]
                    inner = getattr(img_obj, "image", None)
                    blob = getattr(inner, "image_bytes", None) if inner else None
                    if isinstance(blob, bytes):
                        image_path.write_bytes(blob)
                        log.info("expression: image saved via %s", model_name)
                        return True, model_name

        except Exception as e:
            err_str = str(e)
            short = err_str[:240]
            if _is_quota_error(err_str):
                log.warning(
                    "expression: %s — key quota exhausted, trying next key. "
                    "Detail: %s", model_name, short,
                )
                # Same key won't recover for subsequent models — bail.
                return False, ""
            elif "INVALID_ARGUMENT" in err_str and "paid" in err_str.lower():
                log.warning(
                    "expression: %s requires paid plan — skipping. Detail: %s",
                    model_name, short,
                )
            elif "NOT_FOUND" in err_str or "404" in err_str:
                log.warning(
                    "expression: %s not available on this API version. "
                    "Detail: %s", model_name, short,
                )
            else:
                log.warning(
                    "expression: %s call failed: %s", model_name, short,
                )
            continue

    return False, ""


def _call_openai_image(api_key: str, base_url: str, prompt_text: str,
                       image_path: Path) -> tuple[bool, str]:
    """Single-key OpenAI image attempt. Returns (success, model_name).

    Cascades through OpenAI image models: gpt-image-2 (newest, may
    not exist yet on every account) → gpt-image-1 (April 2025) →
    dall-e-3 (fallback). 404s on the newer names just fall through
    to the next, so ordering by recency is safe.

    Param differences: gpt-image-* always returns b64_json and rejects
    the `response_format` parameter; dall-e-3 returns URLs by default
    so we explicitly ask for b64_json. Same .data[0].b64_json read on
    both paths.

    Quota error short-circuits the cascade — billing limits are at
    the key level, not per-model.
    """
    try:
        from openai import OpenAI
    except ImportError:
        log.warning(
            "expression: `openai` package not installed — "
            "OpenAI fallback unavailable"
        )
        return False, ""

    import base64

    candidates = [
        ("gpt-image-2", False),
        ("gpt-image-1", False),
        ("dall-e-3",    True),
    ]

    client = OpenAI(api_key=api_key, base_url=base_url or None)

    for model_name, use_response_format in candidates:
        try:
            kwargs = {
                "model": model_name,
                "prompt": prompt_text,
                "n": 1,
                "size": "1024x1024",
            }
            if use_response_format:
                kwargs["response_format"] = "b64_json"
            result = client.images.generate(**kwargs)
            if result.data and result.data[0].b64_json:
                image_path.write_bytes(base64.b64decode(result.data[0].b64_json))
                log.info("expression: image saved via %s", model_name)
                return True, model_name
            log.warning("expression: %s returned no image data", model_name)
        except Exception as e:
            err_str = str(e)
            short = err_str[:240]
            if _is_quota_error(err_str):
                log.warning(
                    "expression: %s — key quota/billing exhausted, "
                    "trying next key. Detail: %s", model_name, short,
                )
                return False, ""
            if "model" in err_str.lower() and (
                "not found" in err_str.lower()
                or "does not exist" in err_str.lower()
                or "404" in err_str
            ):
                log.info(
                    "expression: %s unavailable on this account, "
                    "cascading. Detail: %s", model_name, short,
                )
            else:
                log.warning(
                    "expression: %s call failed: %s", model_name, short,
                )
            continue

    return False, ""


def _provider_image_backend(provider: dict):
    """Return a callable(api_key) → (ok, model) for the provider's
    image API, or None if the provider isn't image-capable."""
    ptype = (provider.get("type") or "").lower()
    if ptype == "gemini":
        return lambda key, prompt, path: _call_gemini_image(key, prompt, path)
    if ptype == "openai_compat":
        # Only the real OpenAI endpoint is known to host DALL-E. Other
        # OpenAI-compatible endpoints (OpenRouter, Groq, DeepSeek) are
        # text-only — skip silently rather than 404 noisily.
        base_url = provider.get("base_url") or ""
        if "api.openai.com" in base_url:
            return lambda key, prompt, path: _call_openai_image(
                key, base_url, prompt, path,
            )
    return None


def _generate_image(prompt_text: str, image_path: Path) -> tuple[bool, str]:
    """Multi-key, multi-provider fallback. Returns (success, model_name).

    Walks LLM_PROVIDERS in configured order. For each enabled,
    image-capable provider, tries every API key (comma-separated in
    the `api_key` field) until one produces an image. On a quota
    error the next key is tried; if all keys fail, control falls
    through to the next provider.
    """
    try:
        from sentinel import config
    except Exception as e:
        log.warning("expression: could not load config: %s", e)
        return False, ""

    attempted = 0
    for provider in config.LLM_PROVIDERS:
        if not provider.get("enabled"):
            continue
        backend = _provider_image_backend(provider)
        if backend is None:
            continue
        keys = _iter_api_keys(provider)
        if not keys:
            continue

        name = provider.get("name") or "?"
        for idx, key in enumerate(keys, start=1):
            attempted += 1
            log.info(
                "expression: trying %s key %d/%d", name, idx, len(keys),
            )
            ok, model = backend(key, prompt_text, image_path)
            if ok:
                return True, model

    if attempted == 0:
        log.warning(
            "expression: no image-capable provider configured "
            "(need Gemini or OpenAI with an api_key)"
        )
    else:
        log.warning(
            "expression: all %d key attempts exhausted — no image produced",
            attempted,
        )
    return False, ""


# ── Kind selection ─────────────────────────────────────────────────


def _pick_kind() -> str:
    """Slime picks what to draw. Weighted toward self-portrait early
    (so the user gets a feel for the slime's own personality first),
    shifting toward master-portrait + us-portrait as relationship
    accumulates."""
    try:
        from sentinel.expression.album import list_recent
        existing = list_recent(limit=10)
    except Exception:
        existing = []

    n = len(existing)

    if n == 0:
        # First ever — always self-portrait. Slime introduces itself
        # before claiming to know the master.
        return ExpressionKind.SELF_PORTRAIT

    # Weighted random based on accumulated history.
    weights = {
        ExpressionKind.SELF_PORTRAIT:   max(1.0, 4.0 - n * 0.3),  # decays
        ExpressionKind.MASTER_PORTRAIT: min(3.0, 1.0 + n * 0.2),  # grows
        ExpressionKind.US_PORTRAIT:     min(2.0, 0.5 + n * 0.15),
    }
    kinds = list(weights.keys())
    ws = [weights[k] for k in kinds]
    return random.choices(kinds, weights=ws, k=1)[0]


# ── Public API ─────────────────────────────────────────────────────


def generate_expression(kind: Optional[str] = None) -> Optional[Expression]:
    """Run the full pipeline. `kind` defaults to Slime's own choice."""
    if kind is None:
        kind = _pick_kind()
    if kind not in ExpressionKind.ALL:
        log.error("unknown expression kind: %s", kind)
        return None

    # Step 1: Slime writes its own prompt + caption.
    if kind == ExpressionKind.SELF_PORTRAIT:
        result = prompts.render_self_portrait_prompt()
    elif kind == ExpressionKind.MASTER_PORTRAIT:
        result = prompts.render_master_portrait_prompt()
    else:
        result = prompts.render_us_portrait_prompt()

    if not result:
        log.warning("prompt generation failed for %s", kind)
        return None
    visual_prompt, caption = result

    # Step 2: allocate id + image path before API call so we know
    # where the binary will live.
    EXPRESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    eid = new_id()
    image_path = EXPRESSIONS_DIR / f"{eid}.png"

    # Step 3: actual image generation.
    ok, model = _generate_image(visual_prompt, image_path)
    if not ok:
        # Don't save metadata if no image — we don't want orphan
        # JSON files representing failed attempts.
        return None

    # Step 4: identity snapshot for metadata.
    try:
        from sentinel.evolution import load_evolution
        evo = load_evolution()
        form, title = evo.form, evo.title
    except Exception:
        form, title = "Slime", "初生史萊姆"

    # Step 5: persist.
    exp = Expression(
        id=eid,
        kind=kind,
        slime_form=form,
        slime_title=title,
        prompt=visual_prompt,
        caption=caption,
        image_path=f"{eid}.png",
        model=model,
    )
    save_expression(exp)
    log.info("expression generated: %s (%s) — %s", eid, kind, caption[:40])
    return exp


# ── Auto-trigger ───────────────────────────────────────────────────


def maybe_generate_weekly(force: bool = False) -> Optional[Expression]:
    """Run the auto-generation if it's due. Idempotent within a week.

    Cadence: at most one auto-generated expression per week. Called
    by the scheduler / on app start; if an expression has already
    been generated within the last 6 days we no-op.

    `force=True` bypasses the cadence check (used by debug / "請畫
    一張" buttons).
    """
    if not force:
        try:
            from sentinel.expression.album import list_recent
            recent = list_recent(limit=1)
            if recent:
                latest = recent[0]
                age_days = (time.time() - latest.generated_at) / 86400
                if age_days < 6:
                    log.debug(
                        "skipping weekly expression — last one was %.1f days ago",
                        age_days,
                    )
                    return None
        except Exception as e:
            log.warning("cadence check failed: %s — generating anyway", e)

    return generate_expression()
