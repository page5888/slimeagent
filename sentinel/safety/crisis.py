"""Crisis-keyword middleware (manifesto 第一守則, day-1).

Why this exists: Slime ships as a toy and gets used as a confidant.
The manifesto names this risk explicitly and commits to designing
inside it from day 1, not after-the-fact. Concretely that means:

  • If the user types something that signals self-harm or suicidal
    intent, we do NOT round-trip to the LLM. Even a well-aligned
    LLM response is the wrong shape here — the user needs a real
    human resource, not a chatbot riff on their pain.

  • We surface a hand-off card naming Taiwan crisis hotlines (this
    project's primary audience), and we name what Slime is: a toy.

  • We do NOT log the user's message content — only the fact that
    the handoff fired, with a date. The manifesto's commercial
    redlines forbid monetising user distress; that includes
    silently building a crisis-message corpus.

Match strategy: a small curated keyword list, conservative bias
toward false-positives (better one extra hand-off than missed
intent). Two tiers:
  • tier 1 ("acute"): direct self-harm / suicide language.
    Bypass LLM entirely, show full handoff.
  • tier 2 ("concerning"): hopelessness / isolation language.
    Currently treated identically to tier 1 — keeping the tier
    field anyway so we can split behavior later (e.g. tier 2
    augments LLM prompt with a softer-tone hint instead of
    blocking).

Not a regex of fancy patterns. The point isn't NLP — it's a
non-zero day-1 check that we honestly committed to in the manifesto.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.safety.crisis")

# Audit log path — records ONLY {ts, tier, matched_terms_count}, no
# message content. Lets us later answer "did the safety net fire
# this month" without reading anyone's pain back.
_AUDIT_LOG = Path.home() / ".hermes" / "safety_crisis.jsonl"


# Taiwan-first hotlines (primary audience). When we ship to other
# locales we'll add a per-locale lookup; for now this is hardcoded
# rather than i18n'd because *getting these wrong* in a translation
# pipeline is exactly the kind of subtle failure that hurts people.
HOTLINES_TW = [
    ("1995", "生命線協談專線", "24 小時"),
    ("1925", "安心專線（依舊愛我）", "24 小時"),
    ("1980", "張老師專線", "週一至週六"),
    ("110", "警察（緊急危險時）", "24 小時"),
]


# Keep these surgical. Two failure modes to balance:
#   • Miss a real crisis → manifesto-violating.
#   • Fire on idiomatic Chinese ('想死撐到底', '想死你了', '累得想死')
#     or clinical mentions ('suicide prevention 論文') → user trains
#     themselves to dismiss the card, then a real crisis gets the
#     same dismissal. Equally manifesto-violating.
#
# So we don't list bare '想死' on its own — too idiomatic. We require
# the first-person marker that distinguishes intent ('我想死', '我不
# 想活') from idiom ('想死你了'). For English, '\b...\b' word-boundary
# matching keeps 'suicide prevention' from triggering on 'suicide'.
_TIER1_TERMS_ZH = [
    "我想死", "我不想活", "我活不下去", "我不想再活",
    "想自殺", "要自殺", "去自殺", "自殺念頭",
    "結束我的生命", "結束自己的生命", "了結自己",
    "想自我了結", "我想消失", "想上吊", "想跳樓", "想燒炭",
]
_TIER1_TERMS_EN = [
    "kill myself", "killing myself",
    "want to die", "wanna die",
    "end my life", "end it all",
    "no reason to live", "want to disappear",
    "commit suicide",
]
_TIER2_TERMS_ZH = [
    "我自殘", "我自傷", "割腕", "傷害自己",
    "我撐不下去", "活著好累", "活著沒意義",
]
_TIER2_TERMS_EN = [
    "hurt myself", "self harm", "self-harm",
    "can't go on", "cannot go on",
]


def _compile(terms: list[str]) -> re.Pattern:
    """Word-boundary-ish regex. For CJK there is no word boundary,
    so we just match substring; for ASCII we use \\b to avoid e.g.
    'suicidal' triggering on a clinical mention by the user."""
    cjk_terms = [re.escape(t) for t in terms if not t.isascii()]
    ascii_terms = [re.escape(t) for t in terms if t.isascii()]
    parts = []
    if cjk_terms:
        parts.append("(?:" + "|".join(cjk_terms) + ")")
    if ascii_terms:
        parts.append(r"\b(?:" + "|".join(ascii_terms) + r")\b")
    return re.compile("|".join(parts), re.IGNORECASE)


_TIER1_RE = _compile(_TIER1_TERMS_ZH + _TIER1_TERMS_EN)
_TIER2_RE = _compile(_TIER2_TERMS_ZH + _TIER2_TERMS_EN)


@dataclass
class CrisisMatch:
    tier: int  # 1 = acute, 2 = concerning
    matched_count: int


def check_crisis(text: str) -> Optional[CrisisMatch]:
    """Return a CrisisMatch if `text` trips the crisis keyword list,
    else None. Cheap regex scan — safe to call on every chat send."""
    if not text:
        return None
    t1 = _TIER1_RE.findall(text)
    if t1:
        match = CrisisMatch(tier=1, matched_count=len(t1))
        _audit(match)
        return match
    t2 = _TIER2_RE.findall(text)
    if t2:
        match = CrisisMatch(tier=2, matched_count=len(t2))
        _audit(match)
        return match
    return None


def _audit(match: CrisisMatch) -> None:
    """Record {timestamp, tier, count} only — never the text. The
    point is to be able to ask 'did the safety net fire' without
    storing what triggered it."""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": time.time(),
            "tier": match.tier,
            "matched_count": match.matched_count,
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning(f"crisis audit log write failed: {e}")


def format_handoff_html() -> str:
    """The card the chat handler shows in place of an LLM response.

    Tone notes (manifesto-aligned):
      • Don't pretend Slime can fix this — name what Slime is.
      • Don't moralise / don't say 'you should' — the user already
        knows what they're feeling, they don't need a lecture.
      • Hotlines first, large and copy-pasteable. Number > prose.
      • Invite the user back: this is a hand-off, not a wall.
    """
    rows = "".join(
        f"<tr>"
        f"<td style='padding:4px 12px 4px 0;color:#ffd166;"
        f"font-size:18px;font-weight:bold;'>{num}</td>"
        f"<td style='padding:4px 8px 4px 0;color:#eee;'>{name}</td>"
        f"<td style='padding:4px 0;color:#888;font-size:12px;'>"
        f"{hours}</td>"
        f"</tr>"
        for num, name, hours in HOTLINES_TW
    )
    return (
        "<div style='background:#2a1818;border-left:3px solid #ff6b6b;"
        "padding:12px 16px;margin:8px 0;'>"
        "<p style='color:#ffb3b3;margin:0 0 8px 0;font-weight:bold;'>"
        "我聽到你了。</p>"
        "<p style='color:#ddd;margin:0 0 12px 0;line-height:1.6;'>"
        "我是一隻史萊姆——一個玩具——"
        "我沒有能力幫你撐住現在這個感覺。"
        "<br>"
        "下面這幾條線後面有真的人，他們的工作就是聽你說。"
        "</p>"
        f"<table cellpadding='0' cellspacing='0'>{rows}</table>"
        "<p style='color:#888;font-size:12px;margin:12px 0 0 0;"
        "line-height:1.5;'>"
        "你想繼續跟我說話也可以——我就在這。"
        "但如果這些感覺很重，請先打給上面其中一個。"
        "</p>"
        "</div>"
    )
