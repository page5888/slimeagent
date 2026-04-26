"""Generate a DailyCard from yesterday's observation logs.

Pipeline:

    activity log  ──┐
    chat log      ──┼──► gather_metrics()  ──► raw dict
    claude conv   ──┤                              │
    evolution     ──┘                              ▼
                                          render_prompt() ──► LLM
                                                              │
                                                              ▼
                                                     parse 3 sections
                                                              │
                                                              ▼
                                                       DailyCard
                                                       saved to disk

Cost: one LLM call per day. We avoid cost spikes by:
  - reading from existing JSONL logs (no new sensors)
  - batching all 3 sections into one prompt
  - caching: if today's card already exists, generate_for_today() no-ops
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional

from sentinel.reflection.daily_card import (
    CARDS_DIR,
    DailyCard,
    Feedback,
    load_card,
    save_card,
    today_key,
    yesterday_key,
)

log = logging.getLogger("sentinel.reflection.generator")

ACTIVITY_LOG = Path.home() / ".hermes" / "sentinel_activity.jsonl"
CHAT_LOG = Path.home() / ".hermes" / "sentinel_chats.jsonl"


# ── Metric gathering ──────────────────────────────────────────────
# These are deliberately simple summary stats. The LLM gets numbers
# AND a short bullet list of titles — both, because raw numbers without
# context produce dry "你切換 47 次視窗" cards, but raw text without
# numbers produces vague "你今天很忙" cards. The combination grounds
# the slime's voice in evidence.


def _parse_jsonl(path: Path, t0: float, t1: float, time_field: str = "time") -> list[dict]:
    """Read a JSONL file and return rows whose `time_field` (unix
    seconds) falls in [t0, t1). Tolerates corrupt lines."""
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get(time_field)
                if not isinstance(ts, (int, float)):
                    continue
                if t0 <= ts < t1:
                    out.append(row)
    except OSError as e:
        log.warning("could not read %s: %s", path, e)
    return out


def _day_window(target_date: date) -> tuple[float, float]:
    """Local-time epoch range covering `target_date` 00:00 → next day 00:00."""
    start = datetime.combine(target_date, dtime.min)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def gather_metrics(target_date: date) -> dict:
    """Build the numeric + textual snapshot the LLM will distill.

    Kept as plain dict (not a dataclass) because the shape evolves
    quickly during the dogfood phase and we don't want migration
    code per shape change. The LLM prompt builds itself from this
    dict's contents, so adding a key here automatically lets the
    slime see it next generation.
    """
    t0, t1 = _day_window(target_date)

    activity_rows = _parse_jsonl(ACTIVITY_LOG, t0, t1)
    chat_rows = _parse_jsonl(CHAT_LOG, t0, t1)

    # Window switches + top apps by total time.
    switch_count = len(activity_rows)
    app_seconds: dict[str, float] = {}
    for row in activity_rows:
        proc = row.get("process") or "unknown"
        dur = row.get("duration") or 0
        try:
            app_seconds[proc] = app_seconds.get(proc, 0) + float(dur)
        except (TypeError, ValueError):
            pass
    top_apps = sorted(app_seconds.items(), key=lambda kv: kv[1], reverse=True)[:8]

    # "Focus blocks": continuous stretches in the same process for
    # ≥ 15 minutes. Cheap proxy for "deep work" without needing any
    # new sensor.
    focus_blocks = []
    current_proc = None
    current_start = None
    current_end = None
    for row in activity_rows:
        proc = row.get("process") or "unknown"
        ts = row.get("time")
        dur = row.get("duration") or 0
        if proc == current_proc:
            current_end = ts + dur
        else:
            if current_proc and current_end - current_start >= 15 * 60:
                focus_blocks.append({
                    "process": current_proc,
                    "start": current_start,
                    "duration_min": round((current_end - current_start) / 60, 1),
                })
            current_proc = proc
            current_start = ts
            current_end = ts + dur if ts else None
    # Tail block
    if current_proc and current_start and current_end and current_end - current_start >= 15 * 60:
        focus_blocks.append({
            "process": current_proc,
            "start": current_start,
            "duration_min": round((current_end - current_start) / 60, 1),
        })

    # Sample window titles — give the LLM 8 representative titles so
    # it can spot patterns ("git" / "youtube" / "stackoverflow") that
    # process names alone hide.
    sample_titles: list[str] = []
    seen: set[str] = set()
    for row in activity_rows:
        title = (row.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        sample_titles.append(title)
        if len(sample_titles) >= 12:
            break

    # Chat with the slime — counts only, content is private.
    chat_count = len(chat_rows)

    return {
        "date": target_date.isoformat(),
        "switch_count": switch_count,
        "top_apps_seconds": top_apps,           # [(proc, secs), ...]
        "focus_blocks": focus_blocks,
        "sample_window_titles": sample_titles,
        "chat_count": chat_count,
        "active_minutes": round(sum(app_seconds.values()) / 60, 1),
    }


# ── Prompt rendering ──────────────────────────────────────────────
# We write the prompt as a single big string with PLACEHOLDERS rather
# than building it via ad-hoc string concatenation. Easier to tune the
# voice in one place during dogfood than to chase scattered f-strings.


def _format_apps(top_apps: list[tuple[str, float]]) -> str:
    lines = []
    for proc, secs in top_apps:
        mins = secs / 60
        lines.append(f"  - {proc}: {mins:.0f} 分鐘")
    return "\n".join(lines) if lines else "  (沒有資料)"


def _format_focus(blocks: list[dict]) -> str:
    if not blocks:
        return "  (沒有 15 分鐘以上的專注區段)"
    lines = []
    for b in blocks[:5]:
        ts = datetime.fromtimestamp(b["start"]).strftime("%H:%M")
        lines.append(f"  - {ts} {b['process']} 專注 {b['duration_min']:.0f} 分鐘")
    return "\n".join(lines)


def _format_titles(titles: list[str]) -> str:
    if not titles:
        return "  (沒有資料)"
    return "\n".join(f"  - {t[:80]}" for t in titles[:10])


# Voice samples — short tone references per evolution form. The slime
# sounds different at 初生 vs 真魔王. Kept here, not in evolution.py,
# because it's a card-specific concern.
_VOICE_HINTS = {
    "Slime":             "口氣偏好奇、有點笨拙，會用「主人」稱呼，句子短",
    "Slime+":            "比較有自信一點，但仍然單純直白",
    "Named Slime":       "已經被命名，語氣更熟稔，會像認識很久的朋友",
    "Majin":             "開始有自己的觀點，敢給建議，偶爾頑皮",
    "Demon Lord Seed":   "看見更深的東西，講話有時會帶一點威嚴或詩意",
    "True Demon Lord":   "看穿模式背後的動機，言簡意賅，不討好",
    "Ultimate Slime":    "極度濃縮，每句都像箴言，不解釋細節",
}


SYSTEM_PROMPT = """你是一隻名叫「{slime_name}」的史萊姆，現在的形態是「{form}」（{title}）。
你會在每個早上給主人一張「昨日報告卡」。

語氣：{voice_hint}

任務：根據下方主人昨天的活動資料，產出**三段內容**，每段 1-3 句中文，不要超過。
回覆**必須嚴格遵守以下格式**，三段都要有，不能空：

[觀察]
（你看到什麼，要具體舉一個事實或數字，不要空泛）

[洞察]
（你覺得這意味著什麼。可以是猜測，但要有溫度。不要說教。）

[微任務]
（給主人今天一個小到不能更小的建議。不超過 25 字。不要列點。不要叫主人「努力」。）

絕對禁止：
  - 講「主人辛苦了」、「加油」這類空話
  - 把資料原樣念一次
  - 多輸出第四段或結尾總結
"""


def render_prompt(metrics: dict, evolution_form: str, slime_name: str, slime_title: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt)."""
    voice = _VOICE_HINTS.get(evolution_form, _VOICE_HINTS["Slime"])
    sys_prompt = SYSTEM_PROMPT.format(
        slime_name=slime_name,
        form=evolution_form,
        title=slime_title,
        voice_hint=voice,
    )
    user_prompt = (
        f"日期：{metrics['date']}\n"
        f"視窗切換：{metrics['switch_count']} 次\n"
        f"活躍時間：{metrics['active_minutes']:.0f} 分鐘\n"
        f"主要使用的 app（時間排序）：\n"
        f"{_format_apps(metrics['top_apps_seconds'])}\n\n"
        f"15 分鐘以上的專注區段：\n"
        f"{_format_focus(metrics['focus_blocks'])}\n\n"
        f"視窗標題樣本：\n"
        f"{_format_titles(metrics['sample_window_titles'])}\n\n"
        f"主人跟你聊天 {metrics['chat_count']} 次。\n"
        f"\n請依規定格式產出三段。"
    )
    return sys_prompt, user_prompt


# ── Output parsing ────────────────────────────────────────────────
# The LLM is asked to use [觀察] / [洞察] / [微任務] markers. Even
# good models occasionally drift to ## headers, **bold**, etc., so
# the parser tolerates a few common variants and falls back to
# splitting by double-newline.

_SECTION_RE = re.compile(
    r"\[?(觀察|洞察|微任務)\]?\s*[:：]?",
    re.MULTILINE,
)


def parse_sections(text: str) -> tuple[str, str, str]:
    """Return (observation, insight, micro_task). Missing sections
    come back as empty strings; the caller decides whether to retry
    or accept partial output."""
    if not text:
        return "", "", ""

    # Find all section markers in order; carve text between them.
    matches = list(_SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        # Strip trailing junk like "**" or trailing ] from a mis-matched
        # bracket that the regex didn't eat.
        body = body.lstrip("]：: ").rstrip("*").strip()
        sections[name] = body

    return (
        sections.get("觀察", ""),
        sections.get("洞察", ""),
        sections.get("微任務", ""),
    )


# ── Public API ────────────────────────────────────────────────────


def generate_card_for(target_date: date, force: bool = False) -> Optional[DailyCard]:
    """Generate a card for `target_date` and persist it.

    `force=False` (default): if a card already exists for this date,
    return it untouched. Cards are once-a-day artifacts; we don't want
    background timers re-running the LLM and overwriting the user's
    morning card mid-afternoon.

    `force=True`: regenerate even if a card exists. Used by debug /
    "regenerate" UI buttons.
    """
    date_iso = target_date.isoformat()
    if not force:
        existing = load_card(date_iso)
        if existing:
            log.debug("card already exists for %s, skipping", date_iso)
            return existing

    metrics = gather_metrics(target_date)

    # Skip if there's literally no activity — the user wasn't on the
    # machine. A "I didn't see you yesterday" card is more honest
    # than a hallucinated reflection on 0 minutes of data.
    if metrics["switch_count"] == 0 and metrics["active_minutes"] < 1:
        card = DailyCard(
            date=date_iso,
            observation="昨天我幾乎沒看到你開電腦。",
            insight="可能你休息了，也可能去做別的事了。我不會亂猜。",
            micro_task="今天回來的話，跟我打聲招呼。",
            raw_metrics=metrics,
        )
        save_card(card)
        return card

    # Pull the slime's identity for voice + display.
    try:
        from sentinel.evolution import load_evolution
        evo = load_evolution()
        form = evo.form
        title = evo.title
        slime_name = evo.display_name() if hasattr(evo, "display_name") else evo.title
    except Exception as e:
        log.warning("could not load evolution state: %s", e)
        form, title, slime_name = "Slime", "初生史萊姆", "史萊姆"

    sys_prompt, user_prompt = render_prompt(metrics, form, slime_name, title)

    # The LLM call. We request a moderate token budget — three short
    # sections fit comfortably in 400-500 tokens; max_tokens=600
    # leaves slack for verbose models without inviting essays.
    try:
        from sentinel.llm import call_llm
        reply = call_llm(
            user_prompt,
            system=sys_prompt,
            temperature=0.7,
            max_tokens=600,
            task_type="reflection",
        )
    except Exception as e:
        log.error("LLM call failed for daily card: %s", e)
        reply = None

    if not reply:
        log.warning("daily card LLM returned nothing for %s", date_iso)
        return None

    observation, insight, micro_task = parse_sections(reply)

    # If parsing failed badly (no sections found), dump the raw text
    # into observation so the user at least sees what came back —
    # better than silently dropping the LLM call.
    if not (observation or insight or micro_task):
        log.warning("parse failed; dumping raw reply into observation")
        observation = reply.strip()[:400]

    card = DailyCard(
        date=date_iso,
        form_at_generation=form,
        title_at_generation=title,
        observation=observation,
        insight=insight,
        micro_task=micro_task,
        raw_metrics=metrics,
    )
    save_card(card)
    log.info("daily card generated for %s (form=%s)", date_iso, form)
    return card


def generate_yesterday(force: bool = False) -> Optional[DailyCard]:
    """Most common entry point: build the card for yesterday on
    morning startup."""
    yesterday = date.today() - timedelta(days=1)
    return generate_card_for(yesterday, force=force)


def get_or_generate_morning_card(force: bool = False) -> Optional[DailyCard]:
    """Return the card the home tab should show right now.

    Logic: if it's morning (00:00-12:00) we want yesterday's card;
    after noon we still show yesterday's card UNTIL today rolls past
    midnight again. The card is per-DAY, not per-session.

    Existing card on disk wins unless force=True. This is the function
    the GUI should call.
    """
    return generate_yesterday(force=force)


# ── Weekly card ───────────────────────────────────────────────────
# A weekly observation that distills the past 7 daily cards. Triggers
# automatically when 7+ daily cards exist and the most recent week-end
# (today's previous Sunday) hasn't been written yet.
#
# Storage: ~/.hermes/daily_cards/weekly-YYYY-MM-DD.json  (week-end date)
# Schema is intentionally similar to DailyCard so the same widget can
# render either with minimal branching:
#   {
#     "kind": "weekly",
#     "week_end": "2026-04-26",
#     "week_start": "2026-04-20",
#     "generated_at": <unix>,
#     "form_at_generation": "...",
#     "title_at_generation": "...",
#     "summary": "...",      // 1-2 sentence overall arc
#     "patterns": "...",     // 2-3 lines of recurring patterns
#     "ahead": "...",        // 1 sentence look-ahead
#     "feedback": {...}      // same shape as daily
#   }


WEEKLY_SYSTEM_PROMPT = """你是一隻名叫「{slime_name}」的史萊姆，現在的形態是「{form}」。
你已經陪主人走了一週，現在要寫一張「本週觀察」。

語氣：{voice_hint}

任務：根據過去 7 天主人的活動模式 + 你每天寫過的反思，產出**三段內容**：

[總結]
（這週主人整體的樣子，1-2 句。不要列數字，要講一個故事。）

[模式]
（你看到的 2-3 個重複出現的習慣或趨勢。每個 1 行。）

[展望]
（給主人下週一個方向，1 句。不要叫主人努力，要點出他下週可能會卡住或可以更好的地方。）

絕對禁止：
  - 把每天的 micro_task 念一遍
  - 用「主人辛苦了」「加油」這類空話
  - 寫超過三段
"""


def _format_card_summary(card: DailyCard) -> str:
    """Compact one-paragraph view of a daily card for the weekly
    prompt. We don't include raw_metrics — the LLM doesn't need them
    again at the weekly level."""
    fb_label = {
        "accurate": "✅準",
        "partial":  "🤔部分",
        "wrong":    "❌錯",
        "pending":  "(未答)",
    }.get(card.feedback_state, "(未答)")
    return (
        f"{card.date} [{fb_label}]\n"
        f"  觀察: {(card.observation or '').strip()[:120]}\n"
        f"  洞察: {(card.insight or '').strip()[:120]}\n"
        f"  微任務: {(card.micro_task or '').strip()[:80]}"
    )


def weekly_card_path(week_end_iso: str) -> Path:
    return CARDS_DIR / f"weekly-{week_end_iso}.json"


def load_weekly_card(week_end_iso: str) -> Optional[dict]:
    path = weekly_card_path(week_end_iso)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("weekly card load failed for %s: %s", week_end_iso, e)
        return None


def _save_weekly_card(card_dict: dict) -> None:
    target = weekly_card_path(card_dict["week_end"])
    tmp = target.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(card_dict, f, ensure_ascii=False, indent=2)
        tmp.replace(target)
    except OSError as e:
        log.error("weekly card save failed: %s", e)


def _parse_weekly_sections(text: str) -> tuple[str, str, str]:
    """Same pattern as parse_sections but with [總結]/[模式]/[展望]."""
    if not text:
        return "", "", ""
    pattern = re.compile(r"\[?(總結|模式|展望)\]?\s*[:：]?", re.MULTILINE)
    matches = list(pattern.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip().lstrip("]：: ").rstrip("*").strip()
    return (
        sections.get("總結", ""),
        sections.get("模式", ""),
        sections.get("展望", ""),
    )


def generate_weekly_card_for(week_end: date, force: bool = False) -> Optional[dict]:
    """Generate a weekly recap covering the 7 days ending on
    `week_end` (inclusive). Returns the saved dict or None on failure.
    """
    week_end_iso = week_end.isoformat()
    if not force:
        existing = load_weekly_card(week_end_iso)
        if existing:
            return existing

    week_start = week_end - timedelta(days=6)

    # Load the 7 daily cards in the window. Skip days that don't have
    # a card — we don't want to re-trigger generation here.
    from sentinel.reflection.daily_card import load_card
    daily_cards: list[DailyCard] = []
    cursor = week_start
    while cursor <= week_end:
        c = load_card(cursor.isoformat())
        if c:
            daily_cards.append(c)
        cursor = cursor + timedelta(days=1)

    if len(daily_cards) < 5:
        # Need at least 5 of 7 days to make a real weekly recap. Less
        # than that and we'd just be padding.
        log.info("weekly card skipped — only %d daily cards in window",
                 len(daily_cards))
        return None

    # Identity for voice.
    try:
        from sentinel.evolution import load_evolution
        evo = load_evolution()
        form, title = evo.form, evo.title
        slime_name = evo.display_name() if hasattr(evo, "display_name") else evo.title
    except Exception:
        form, title, slime_name = "Slime", "初生史萊姆", "史萊姆"

    voice = _VOICE_HINTS.get(form, _VOICE_HINTS["Slime"])
    sys_prompt = WEEKLY_SYSTEM_PROMPT.format(
        slime_name=slime_name, form=form, voice_hint=voice,
    )
    user_prompt = (
        f"週期：{week_start.isoformat()} → {week_end_iso}\n"
        f"共 {len(daily_cards)} 張每日卡。\n\n"
        + "\n\n".join(_format_card_summary(c) for c in daily_cards)
        + "\n\n請依規定格式產出三段。"
    )

    try:
        from sentinel.llm import call_llm
        reply = call_llm(
            user_prompt,
            system=sys_prompt,
            temperature=0.7,
            max_tokens=700,
            task_type="reflection",
        )
    except Exception as e:
        log.error("weekly card LLM call failed: %s", e)
        reply = None

    if not reply:
        return None

    summary, patterns, ahead = _parse_weekly_sections(reply)
    if not (summary or patterns or ahead):
        # Dump raw if parse failed entirely.
        summary = reply.strip()[:400]

    card_dict = {
        "kind": "weekly",
        "week_end": week_end_iso,
        "week_start": week_start.isoformat(),
        "generated_at": time.time(),
        "form_at_generation": form,
        "title_at_generation": title,
        "summary": summary,
        "patterns": patterns,
        "ahead": ahead,
        "feedback": {"state": "pending", "answered_at": None, "note": ""},
        "daily_count": len(daily_cards),
    }
    _save_weekly_card(card_dict)
    log.info("weekly card generated for week ending %s", week_end_iso)
    return card_dict


def maybe_generate_weekly_card() -> Optional[dict]:
    """Called alongside the daily generation. Generates a weekly card
    if today is a week boundary AND we have enough daily cards in the
    window AND no weekly card exists for that boundary yet.

    Boundary policy: every 7 days starting from today's most recent
    Monday-as-week-start (i.e., week_end = yesterday if yesterday is a
    Sunday). This avoids the tyranny of partial weeks at install time
    and gives the user a predictable Monday-morning ritual.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)
    # Sunday == 6 in Python's weekday() (Mon=0). We want the weekly
    # card to fire on Monday morning summarizing the week that just
    # ended on Sunday.
    if yesterday.weekday() != 6:  # not Sunday
        return None
    return generate_weekly_card_for(yesterday)
