"""Daily Slime Reflection Card — the v0.7 wedge.

Every morning the slime hands you a card summarizing yesterday:
  - 觀察 (what I saw)
  - 洞察 (what I think it means)
  - 今日微任務 (one small thing for today)

You answer "✅ 準 / 🤔 有點像 / ❌ 不對" and the slime learns.

This package is intentionally narrow. It does:
  1. Read yesterday's existing observation logs (activity, system,
     claude convo, file events) — no new sensors.
  2. Distill them with the slime's evolution-stage voice into 3
     short sections.
  3. Persist the card + your feedback to ~/.hermes/daily_cards/.

It does NOT do:
  - Real-time alerts (the card is a once-a-day ritual)
  - Routine fire / approval (that's still the routines/ subsystem)
  - Server-side analytics

Why a separate package and not part of routines/reflection.py?
  - reflection.py is about meta-cognition over the routine system
    itself ("which routines are noisy"). This is meta-cognition over
    the user. Different addressee.
  - Keeping it isolated lets us delete or pivot the whole module
    without touching the autonomy loop.
"""
from sentinel.reflection.daily_card import (
    DailyCard,
    Feedback,
    load_card,
    save_card,
    today_key,
    yesterday_key,
)

__all__ = [
    "DailyCard",
    "Feedback",
    "load_card",
    "save_card",
    "today_key",
    "yesterday_key",
]
