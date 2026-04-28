"""Generator — orchestrates the full pipeline.

Pipeline per expression:
  1. Pick kind (random weighted, or caller-specified)
  2. Slime writes its own prompt + caption (via prompts.py)
  3. Image API call (Gemini Imagen primary, fallback can be added)
  4. Persist binary + metadata
  5. Return Expression object for the container layer to render

Cost model (for v0.7-alpha dogfood — single user, no monetization):
  - one image per call to Gemini Imagen ~ free tier eligible
  - max one auto-generation per week (Sunday evening trigger)
  - manual "請畫一張" allowed but cooldown-gated to prevent spam

API choice:
  Gemini Imagen 3 via google.genai (newer SDK) or google.generativeai
  (older SDK). We probe both at import time and use whichever is
  installed. If neither: graceful degrade — return None so the caller
  can show a "still drawing in your head" placeholder rather than
  crashing.

No Qt imports. No GUI imports.
"""
from __future__ import annotations

import logging
import random
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


def _generate_image_gemini(prompt_text: str, image_path: Path) -> tuple[bool, str]:
    """Call Gemini image generation API. Returns (success, model_name).

    Strategy ordering picks the cheapest free-tier eligible model
    first so a typical user with no billing gets results. We probed
    the API live (2026-04-28) — `imagen-3.0-generate-002` returns
    404, `imagen-4.0-*` requires paid plan. The free-tier path is
    the gemini-flash-image family via generateContent with IMAGE in
    response_modalities.

    Failure modes surfaced explicitly via log.warning so the operator
    can see *why* it didn't draw (rate limit / no key / SDK missing)
    instead of just "all strategies failed".
    """
    # Read Gemini key from saved provider config.
    try:
        from sentinel import config
        api_key = None
        for p in config.LLM_PROVIDERS:
            if (p.get("name") or "").lower() == "gemini" and p.get("api_key"):
                api_key = p["api_key"]
                break
        if not api_key:
            log.warning("expression: no Gemini API key configured")
            return False, ""
    except Exception as e:
        log.warning("expression: could not read Gemini key: %s", e)
        return False, ""

    # Models to try in priority order. First success wins.
    # gemini-2.5-flash-image is the most stable free-tier option.
    # gemini-3.1-flash-image-preview is newer but preview-tier.
    # imagen-4.0-fast-generate-001 is paid (~$0.02/image) — last resort.
    candidates = [
        ("gemini-2.5-flash-image",          "generate_content"),
        ("gemini-3.1-flash-image-preview",  "generate_content"),
        ("imagen-4.0-fast-generate-001",    "generate_images"),  # paid
    ]

    try:
        from google import genai as _genai
        from google.genai import types as _types
    except ImportError:
        log.warning(
            "expression: `google.genai` not installed — "
            "run `pip install google-genai` in venv"
        )
        return False, ""

    client = _genai.Client(api_key=api_key)

    for model_name, method in candidates:
        try:
            if method == "generate_content":
                # Gemini-flash-image: text-conditioned image gen via
                # generate_content with IMAGE in response_modalities.
                result = client.models.generate_content(
                    model=model_name,
                    contents=prompt_text,
                    config=_types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                )
                # Walk candidates → parts; the image bytes live under
                # parts[i].inline_data.data with mime_type='image/png'.
                for cand in (result.candidates or []):
                    for part in (cand.content.parts or []):
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            image_path.write_bytes(inline.data)
                            log.info("expression: image saved via %s", model_name)
                            return True, model_name

            elif method == "generate_images":
                # Imagen path (paid). Different return shape: result
                # has .generated_images with .image.image_bytes.
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
            # Surface the reason. 429 = rate limit, 400 = paid plan
            # required, 404 = model retired. Log as warning so the
            # operator sees it; we still try the next candidate.
            err_str = str(e)
            short = err_str[:240]
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                log.warning(
                    "expression: %s — daily/per-minute quota exhausted. "
                    "Free tier resets at 00:00 PT. Detail: %s",
                    model_name, short,
                )
            elif "INVALID_ARGUMENT" in err_str and "paid" in err_str.lower():
                log.warning(
                    "expression: %s requires paid plan — skipping. Detail: %s",
                    model_name, short,
                )
            elif "NOT_FOUND" in err_str or "404" in err_str:
                log.warning(
                    "expression: %s not available on this API version. "
                    "Detail: %s",
                    model_name, short,
                )
            else:
                log.warning(
                    "expression: %s call failed: %s",
                    model_name, short,
                )
            continue

    log.warning(
        "expression: all candidates exhausted — no image produced. "
        "If you saw 429s above, try again tomorrow (daily quota)."
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
    ok, model = _generate_image_gemini(visual_prompt, image_path)
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
