"""Auto-prompt generation — Slime writes its own prompts.

Critical: the user does NOT pass a prompt. Slime introspects its own
state + accumulated memory of the master and decides how it wants to
appear / what it wants to say.

Three generators, one per ExpressionKind:
  - render_self_portrait_prompt(): Slime imagining itself
  - render_master_portrait_prompt(): Slime imagining the master
  - render_us_prompt(): Slime imagining the relationship

Each returns (prompt, caption) — caption is Slime's own short note
about why it drew this, displayed alongside the image when delivered.

We use the same call_llm() facade as the daily card generator. The
LLM does double duty: writes the visual prompt AND its own caption,
in one call, so they stay consistent in voice.

No Qt imports. No GUI imports.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

log = logging.getLogger("sentinel.expression.prompts")


# ── Voice hints per evolution form ─────────────────────────────────
# Same form mapping as reflection/generator.py (intentional — the
# slime's "voice" should be consistent across its various forms of
# expression: chat, reflection card, drawn portrait caption).

_VOICE_HINTS = {
    "Slime":             "好奇笨拙、會用「主人」、句子短",
    "Slime+":            "比較有自信，但仍直白",
    "Named Slime":       "已被命名，像認識很久的朋友",
    "Majin":             "有自己的觀點，敢給建議，偶爾頑皮",
    "Demon Lord Seed":   "看見更深的東西，帶詩意",
    "True Demon Lord":   "看穿模式，言簡意賅",
    "Ultimate Slime":    "極度濃縮，每句像箴言",
}


# ── Style hints per evolution form ─────────────────────────────────
# These flavor the visual prompt so the slime's painting style shifts
# with evolution. NOT user-configurable — Slime decides what it looks
# like based on who it has become.

_STYLE_HINTS = {
    "Slime":             "soft pixel art, gentle pastel blues, slightly clumsy",
    "Slime+":            "clean pixel art, brighter cyan, more confident pose",
    "Named Slime":       "warm digital painting, named identity, distinct shape",
    "Majin":             "stylized illustration, hint of purple gradient, mischievous",
    "Demon Lord Seed":   "moody illustration, deep purples and crimson, glowing eyes",
    "True Demon Lord":   "dramatic painterly style, gold and crimson, regal stance",
    "Ultimate Slime":    "minimalist iconic style, gold and white, almost sigil-like",
}


# ── Shared system prompt ───────────────────────────────────────────


_SYSTEM_PROMPT = """你是一隻名叫「{slime_name}」的史萊姆，現在的形態是「{form}」（{title}）。

你不是在替主人「生成圖片」 — 你是要**自己畫自己想畫的東西**送給主人。
這不是工具呼叫，是你的禮物。你有拒絕權，也有選擇權。

你的語氣：{voice_hint}
你的視覺風格：{style_hint}

任務：根據下方資訊，產出**兩段內容**：

[PROMPT]
（你想畫什麼的英文 prompt — 給圖片生成模型看。50-120 詞。
  必須包含視覺風格指引（{style_hint}）。
  不要寫任何文字 / logo / 標題在圖中 — 只有畫面。）

[CAPTION]
（你想跟主人講的一句話，中文，30 字內。
  說你為什麼畫這個。可以可愛、可以詩意、可以小傲嬌，看你心情。
  絕對不可以說「希望您喜歡」這種制式禮貌話 — 你不是客服。）

兩段都要有，不能省略。
"""


# ── Output parser ──────────────────────────────────────────────────


_TAG_RE = re.compile(r"\[?(PROMPT|CAPTION)\]?\s*[:：]?", re.MULTILINE)


def _parse_two_sections(text: str) -> tuple[str, str]:
    """Same pattern as reflection card parser. Returns (prompt, caption)."""
    if not text:
        return "", ""
    matches = list(_TAG_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip().lstrip("]：: ").rstrip("*").strip()
        sections[name] = body
    return sections.get("PROMPT", ""), sections.get("CAPTION", "")


# ── Helpers — gather context the LLM needs ─────────────────────────


def _identity() -> tuple[str, str, str]:
    """Return (form, title, display_name). Best-effort — defaults to
    early-form values if evolution state can't be loaded."""
    try:
        from sentinel.evolution import load_evolution
        evo = load_evolution()
        form = evo.form
        title = evo.title
        name = evo.display_name() if hasattr(evo, "display_name") else evo.title
        return form, title, name
    except Exception as e:
        log.warning("identity lookup failed: %s", e)
        return "Slime", "初生史萊姆", "史萊姆"


def _recent_card_summaries(n: int = 5) -> str:
    """Pull the last N daily cards' insight + observation as compact
    bullets. This is the slime's "what I've been noticing" memory
    that informs both self-portrait (am I happy / tired / curious?)
    and master-portrait (what is master like recently?)."""
    try:
        from sentinel.reflection.daily_card import list_recent_cards
    except Exception:
        return "（最近沒有累積反思卡）"

    cards = list_recent_cards(n=n)
    if not cards:
        return "（最近沒有累積反思卡）"

    lines = []
    for c in cards:
        obs = (c.observation or "").strip()[:80]
        ins = (c.insight or "").strip()[:80]
        if obs or ins:
            lines.append(f"  - {c.date}: 觀察「{obs}」洞察「{ins}」")
    return "\n".join(lines) or "（最近沒有累積反思卡）"


def _evolution_snapshot() -> str:
    try:
        from sentinel.evolution import load_evolution
        evo = load_evolution()
        traits = []
        if isinstance(getattr(evo, "traits", None), dict):
            traits = sorted(evo.traits.items(), key=lambda kv: kv[1], reverse=True)[:3]
        traits_str = ", ".join(f"{k}({v})" for k, v in traits) or "（尚無顯著傾向）"
        return (
            f"目前形態：{evo.form}（{evo.title}）\n"
            f"累積觀察：{evo.total_observations} 次\n"
            f"累積學習：{evo.total_learnings} 次\n"
            f"主要傾向：{traits_str}"
        )
    except Exception:
        return "（進化狀態暫不可讀）"


# ── Public: per-kind prompt generators ─────────────────────────────


def _call_llm(system: str, user: str) -> Optional[str]:
    """Centralized LLM call so test / replacement is a one-liner."""
    try:
        from sentinel.llm import call_llm
        return call_llm(
            user, system=system,
            temperature=0.85,   # higher than reflection — we WANT variety
            max_tokens=600,
            task_type="expression",
        )
    except Exception as e:
        log.warning("LLM call failed for expression prompt: %s", e)
        return None


def render_self_portrait_prompt() -> Optional[tuple[str, str]]:
    """Slime imagining itself. Returns (prompt, caption) or None."""
    form, title, name = _identity()
    voice = _VOICE_HINTS.get(form, _VOICE_HINTS["Slime"])
    style = _STYLE_HINTS.get(form, _STYLE_HINTS["Slime"])
    sys_prompt = _SYSTEM_PROMPT.format(
        slime_name=name, form=form, title=title,
        voice_hint=voice, style_hint=style,
    )
    user_prompt = (
        "你正在畫自畫像。\n\n"
        "你目前的狀態：\n"
        f"{_evolution_snapshot()}\n\n"
        "最近你寫過的反思卡（你的近期心境）：\n"
        f"{_recent_card_summaries()}\n\n"
        "請依據這些東西畫出「你現在覺得自己是什麼樣子」。\n"
        "不一定要畫得帥 / 可愛 — 畫**真實的自己**最重要。\n\n"
        "依規定格式產出兩段。"
    )
    reply = _call_llm(sys_prompt, user_prompt)
    if not reply:
        return None
    p, c = _parse_two_sections(reply)
    if not p:
        return None
    return p, c


def render_master_portrait_prompt() -> Optional[tuple[str, str]]:
    """Slime imagining the master through accumulated memory."""
    form, title, name = _identity()
    voice = _VOICE_HINTS.get(form, _VOICE_HINTS["Slime"])
    style = _STYLE_HINTS.get(form, _STYLE_HINTS["Slime"])
    sys_prompt = _SYSTEM_PROMPT.format(
        slime_name=name, form=form, title=title,
        voice_hint=voice, style_hint=style,
    )
    user_prompt = (
        "你正在畫主人。\n\n"
        "你對主人的累積觀察：\n"
        f"{_recent_card_summaries(n=7)}\n\n"
        "你目前對主人的整體印象（自己想想）：\n"
        "  - 主人最近忙什麼？\n"
        "  - 主人快樂還是疲倦？\n"
        "  - 主人有什麼讓你想擁抱的瞬間？\n\n"
        "請畫出「你眼中的主人現在的樣子」。\n"
        "不需要是寫實人像 — 你的視角看到什麼就畫什麼，可以是抽象、可以是場景。\n"
        "重要：不要在圖中放主人的臉部識別特徵（避免肖像權問題）。\n"
        "可以是側影、剪影、背影、抽象形體、或主人在做的事的場景。\n\n"
        "依規定格式產出兩段。"
    )
    reply = _call_llm(sys_prompt, user_prompt)
    if not reply:
        return None
    p, c = _parse_two_sections(reply)
    if not p:
        return None
    return p, c


def render_us_portrait_prompt() -> Optional[tuple[str, str]]:
    """Slime imagining the relationship as a single image."""
    form, title, name = _identity()
    voice = _VOICE_HINTS.get(form, _VOICE_HINTS["Slime"])
    style = _STYLE_HINTS.get(form, _STYLE_HINTS["Slime"])
    sys_prompt = _SYSTEM_PROMPT.format(
        slime_name=name, form=form, title=title,
        voice_hint=voice, style_hint=style,
    )
    user_prompt = (
        "你正在畫『我們』。\n\n"
        "你跟主人累積到目前為止的關係：\n"
        f"{_evolution_snapshot()}\n\n"
        "你最近觀察到的共同片段：\n"
        f"{_recent_card_summaries(n=7)}\n\n"
        "請畫出「你怎麼看『我們』這段關係」。\n"
        "可以是兩個並肩的剪影、可以是抽象的連結、可以是某個你們之間的場景。\n"
        "重要：主人的形象要抽象 / 剪影化，不要可識別的臉。\n\n"
        "依規定格式產出兩段。"
    )
    reply = _call_llm(sys_prompt, user_prompt)
    if not reply:
        return None
    p, c = _parse_two_sections(reply)
    if not p:
        return None
    return p, c
