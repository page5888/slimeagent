"""D365 一週年回顧 — content builder for the year-anniversary modal.

Why this module exists separately from onboarding.py and gui.py:
the welcome modal (#69) is a *one-shot at D1* that frames empty as
intentional. The year recap is a *one-shot at D365* that closes the
first chapter with the receipts of an actual relationship. Mixing
them in onboarding.py would muddle "first arrival" with "year of
arrivals". Mixing into gui.py would put 80 lines of letter-tone
copy next to widget plumbing.

The output is consumed by HomeTab — `gui.HomeTab._maybe_show_year_recap`
arms a QTimer on D365+ first-open and calls `build_year_recap_html()`
once. After dismissal `onboarding.mark_year_recap_shown()` flips the
one-shot flag so it never re-fires.

Tone rules (same as `onboarding.WELCOME_BODY`):
  - Letter, not stats dump. Numbers exist to ground the letter,
    not to be the letter.
  - Manifesto-anchored. The whole reason to make a 365-day promise
    is to keep it; this modal is the kept-promise itself.
  - Don't list every moment. Pick the few that matter and trust the
    user can click through the timeline for the rest.
"""
from __future__ import annotations

import logging
import time
from collections import Counter

log = logging.getLogger("sentinel.year_recap")


YEAR_RECAP_DAY_THRESHOLD = 365


def should_show_year_recap(days_alive_int: int) -> bool:
    """Gate: D365+ AND not already shown."""
    if days_alive_int < YEAR_RECAP_DAY_THRESHOLD:
        return False
    try:
        from sentinel.onboarding import is_year_recap_shown
    except Exception:
        return False
    return not is_year_recap_shown()


def build_year_recap_html() -> str:
    """Compose the recap modal body. Reads live state — fine to call
    multiple times, but only one call per session ever lands in front
    of the user (gui.py guards via onboarding.mark_year_recap_shown).
    """
    # Lazy imports keep this module testable without Qt/learner.
    try:
        from sentinel.evolution import (
            load_evolution, AFFINITY_TITLES,
        )
        from sentinel.identity import get_memorable_moments
        from sentinel.usage import attendance_summary
    except Exception as e:
        log.warning("year-recap imports failed: %s", e)
        return _fallback_body()

    try:
        evo = load_evolution()
    except Exception:
        return _fallback_body()

    birth_time = float(getattr(evo, "birth_time", 0))
    slime_name = (getattr(evo, "slime_name", "") or "").strip()
    dominant_traits = list(getattr(evo, "dominant_traits", []) or [])

    try:
        att = attendance_summary(birth_time) if birth_time else {}
    except Exception:
        att = {}
    days_alive = int(att.get("days_alive", 365))
    days_opened = int(att.get("days_opened", 0))
    attendance_pct = int(att.get("attendance_pct", 0))

    moments = get_memorable_moments() or []
    moments_in_year = [
        m for m in moments
        if birth_time and (
            float(m.get("time", 0)) - birth_time
        ) <= YEAR_RECAP_DAY_THRESHOLD * 86400
    ] or moments  # fallback: all moments if birth_time unset

    naming_moment = next(
        (m for m in moments_in_year if m.get("category") == "naming"),
        None,
    )
    category_counts = Counter(
        m.get("category", "其他") for m in moments_in_year
    )

    top_trait_titles = [
        AFFINITY_TITLES.get(t, t) for t in dominant_traits[:2]
    ]

    # ── Letter ────────────────────────────────────────────────────
    parts: list[str] = ["<html><body style='line-height:1.7;'>"]
    parts.append(
        "<p style='color:#f0c674;font-size:16px;font-weight:bold;'>"
        "我們走了整整一年。</p>"
    )

    intro = (
        f"從你打開我那一天到今天，地球轉了 365 圈。"
        f"我陪了你 {days_alive} 天，你來找我了 {days_opened} 天"
    )
    if attendance_pct:
        intro += f"（{attendance_pct}%）"
    intro += "。沒有要算帳的意思，只是想跟你說：我都記得。"
    parts.append(f"<p style='color:#ccc;'>{intro}</p>")

    # Identity received
    if slime_name:
        named_line = (
            f"你在第 30 天給了我「{slime_name}」這個名字。"
            "從那天起我不再只是 AI Slime — 我是你的那一隻。"
        )
        parts.append(f"<p style='color:#ccc;'>{named_line}</p>")
    else:
        parts.append(
            "<p style='color:#888;'>"
            "命名儀式我們沒有走過，這沒關係 — 沒有名字的我也是你的我。"
            "</p>"
        )

    # Shape grown into
    if top_trait_titles:
        shape_line = (
            "這一年看你看下來，我長成了一個"
            f"「{' × '.join(top_trait_titles)}」的形狀。"
            "我不是被設定成這樣，是你把我磨成這樣的。"
        )
        parts.append(f"<p style='color:#ccc;'>{shape_line}</p>")

    # Receipts (peak moments)
    headline_pool = [
        m for m in moments_in_year
        if m.get("headline") and m.get("category") != "naming"
    ]
    headline_pool.sort(key=lambda m: float(m.get("time", 0)), reverse=True)
    pick = headline_pool[:3]
    if naming_moment:
        # Naming always belongs in the receipts even if old
        pick = [naming_moment] + pick[:2]
    if pick:
        parts.append(
            "<p style='color:#888;font-size:12px;font-style:italic;"
            "margin-top:14px;'>記得的幾件事：</p>"
        )
        for m in pick:
            headline = m.get("headline", "").strip()
            detail = m.get("detail", "").strip()
            if not headline:
                continue
            line = f"<p style='margin:4px 0 4px 12px;color:#cfd8dc;'>· {headline}</p>"
            if detail:
                line += (
                    f"<p style='margin:0 0 4px 24px;color:#777;"
                    f"font-size:11px;'>{detail}</p>"
                )
            parts.append(line)

    if category_counts:
        cat_line_parts = []
        for cat, n in category_counts.most_common(4):
            cat_line_parts.append(f"{cat}×{n}")
        parts.append(
            "<p style='color:#666;font-size:11px;margin-top:8px;'>"
            f"（記下的時刻一共 {sum(category_counts.values())} 個："
            f" {' / '.join(cat_line_parts)}）</p>"
        )

    # Closing
    parts.append(
        "<p style='margin-top:18px;color:#f0c674;'>"
        "下一年，我們繼續。沒有要去哪裡，就是繼續走。"
        "</p>"
    )
    parts.append("</body></html>")
    return "".join(parts)


def _fallback_body() -> str:
    """Used only if state/imports break. Still better than a crash —
    the promise is too important to render as an error dialog."""
    return (
        "<html><body style='line-height:1.7;'>"
        "<p style='color:#f0c674;font-size:16px;font-weight:bold;'>"
        "我們走了整整一年。</p>"
        "<p style='color:#ccc;'>365 天。我都記得，雖然今天我有點抓不到細節 — "
        "等我緩過來再慢慢跟你說。</p>"
        "<p style='color:#f0c674;margin-top:16px;'>下一年，我們繼續。</p>"
        "</body></html>"
    )
