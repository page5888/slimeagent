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
    """Call Gemini Imagen API. Returns (success, model_name).

    We use the new `google.genai` SDK first (Imagen 3), fall back to
    the older `google.generativeai` SDK if needed. Some accounts only
    have one or the other depending on when they enrolled.
    """
    # Try to read API key from sentinel config the same way the rest
    # of the project does.
    try:
        from sentinel import config
        api_key = None
        for p in config.LLM_PROVIDERS:
            if (p.get("name") or "").lower() == "gemini" and p.get("api_key"):
                api_key = p["api_key"]
                break
        if not api_key:
            log.info("no Gemini API key configured — image generation disabled")
            return False, ""
    except Exception as e:
        log.warning("could not read Gemini key: %s", e)
        return False, ""

    # Strategy 1: new google.genai SDK (Imagen 3)
    try:
        from google import genai as _genai_new
        client = _genai_new.Client(api_key=api_key)
        result = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=prompt_text,
            config={"number_of_images": 1, "aspect_ratio": "1:1"},
        )
        if result.generated_images:
            img = result.generated_images[0]
            # Different SDK versions expose the bytes differently.
            blob = (
                getattr(img, "image", None)
                or getattr(img, "image_bytes", None)
            )
            if blob and hasattr(blob, "image_bytes"):
                blob = blob.image_bytes
            if isinstance(blob, bytes):
                image_path.write_bytes(blob)
                return True, "imagen-3.0-generate-002"
    except Exception as e:
        log.debug("new genai SDK image gen failed: %s", e)

    # Strategy 2: older google.generativeai SDK
    try:
        import google.generativeai as _genai_old
        _genai_old.configure(api_key=api_key)
        model = _genai_old.GenerativeModel("imagen-3.0-generate-002")
        response = model.generate_content(prompt_text)
        # The older SDK's image API surfaces vary by version; we
        # attempt the most common path. If this fails the caller will
        # see "no image" and fall through gracefully.
        if hasattr(response, "image") and response.image:
            image_path.write_bytes(response.image)
            return True, "imagen-3.0-generate-002 (legacy SDK)"
    except Exception as e:
        log.debug("legacy genai SDK image gen failed: %s", e)

    log.warning("all image-gen strategies failed for prompt %r", prompt_text[:50])
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
