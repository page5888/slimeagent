"""Dry-run preview for the emergent self-mark decision (PR #81 follow-up).

Usage (from repo root):

    python scripts/preview_emergent_self_mark.py

What this does:

  1. Reads your *real* evolution state + memorable_moments (read-only).
  2. Builds the same time-sense signals the daemon would build.
  3. Calls the LLM (real provider, real cost) using the same system
     prompt + JSON schema as the production path.
  4. Prints the prompt, raw reply, and parsed verdict.
  5. **Does not write anything**: no memorable_moment is appended,
     no `last_check` / `last_mark` timestamps are touched, the weekly
     cap is not consumed.

Why bypass the rate caps here:

  The production caps exist so the daemon doesn't burn LLM quota and
  so the timeline doesn't grow daily marks that would dilute "emergent".
  Neither concern applies to a one-shot human-driven preview — you're
  consciously asking "what would the slime say today?" and reading the
  answer yourself. The persistence-side guarantees still hold (this
  script never calls add_memorable_moment).

Why this is *not* a unit test:

  It's a real-data inspection tool. For automated testing we want
  mocked LLMs; that already lives next to the module. This script is
  for "let me eyeball one prompt-response pair against my actual slime."
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `sentinel` importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows default console codec (cp950 on zh-TW) chokes on punctuation
# like 「・」inside the prompt. Force UTF-8 if we can — this is a
# preview tool that exists specifically to print Chinese to a terminal,
# so the encoding has to match the content.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def main() -> int:
    import time
    from sentinel.emergent_self_mark import (
        SYSTEM_PROMPT,
        GUARDRAILS_ZH,
        _build_signals,
        _format_user_prompt,
        _extract_json,
        _output_is_safe,
        MIN_DAYS_ALIVE,
    )

    now = time.time()
    signals = _build_signals(now)

    print("=" * 72)
    print("EMERGENT SELF-MARK PREVIEW")
    print("=" * 72)

    if signals is None:
        print("\nNo signals — likely no birth_time set, or days_alive < "
              f"{MIN_DAYS_ALIVE}. Nothing to preview.")
        return 1

    print("\n── Signals (the time-sense snapshot) ──")
    for k, v in signals.items():
        if isinstance(v, list):
            print(f"  {k}:")
            for item in v:
                print(f"    {item}")
        else:
            print(f"  {k}: {v}")

    # Note: we do NOT check scaffolding-day exclusion here. The point of
    # the preview is to see what the slime would say IF the gate were
    # open — useful for tuning the prompt against scaffolding days too,
    # not just non-scaffolding ones.

    sys_prompt = SYSTEM_PROMPT.format(guardrails=GUARDRAILS_ZH)
    user_prompt = _format_user_prompt(signals)

    print("\n── User prompt sent to LLM ──")
    print(user_prompt)

    print("\n── Calling LLM... ──")
    try:
        from sentinel.llm import call_llm
        reply = call_llm(
            user_prompt,
            system=sys_prompt,
            temperature=0.6,
            max_tokens=300,
            task_type="emergent_self_mark_preview",
        )
    except Exception as e:
        print(f"\n!! LLM call raised {type(e).__name__}: {e}")
        return 2

    if not reply:
        print("\n!! LLM returned no reply (provider error or empty body).")
        return 3

    print("\n── Raw LLM reply ──")
    print(reply)

    parsed = _extract_json(reply)
    if parsed is None:
        print("\n!! Could not extract JSON from reply. In production the "
              "decision would be: SKIP.")
        return 4

    print("\n── Parsed envelope ──")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))

    if not parsed.get("mark"):
        print("\n── Verdict ──")
        print("  Slime would NOT mark today (mark=false).")
        print("  This is the expected common case — most days are not "
              "worth marking.")
        return 0

    headline = str(parsed.get("headline", "") or "").strip()[:120]
    detail = str(parsed.get("detail", "") or "").strip()[:200]

    safe = _output_is_safe(headline, detail)

    print("\n── Verdict ──")
    print(f"  Slime would MARK today.")
    print(f"  headline: {headline!r}")
    print(f"  detail:   {detail!r}")
    print(f"  passes safety filter: {safe}")
    if not safe:
        print("  → In production this would be DROPPED before persisting.")
    print("\n  (Preview only — nothing was written. To let the slime "
          "actually mark, just leave the daemon running; it will fire on "
          "the next idle cycle if the rate caps allow.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
