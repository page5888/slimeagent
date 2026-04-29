"""First-launch ritual + tracking (manifesto-aligned day-1 framing).

Why this module exists separately from gui.py: the welcome ritual
is the moment that **decides whether a new download survives the
first 30 seconds**. Day 1 is structurally hostile to manifesto's
core pitch — "押的是關係的累積" has no data on day 1, so without
explicit framing, the new user sees an empty app that looks
half-finished and closes it. The ritual converts "empty" from a
bug into a feature ("I just arrived. We start at zero on purpose.").

State is persisted to ~/.hermes/onboarding.json with two booleans:
   {welcome_shown: bool, welcome_shown_at: ts}

We never re-show the modal once dismissed; if a user wants to see
it again they can delete the file. Re-showing on every launch
would be hostile.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("sentinel.onboarding")

STATE_FILE = Path.home() / ".hermes" / "onboarding.json"


def is_welcome_shown() -> bool:
    """True if the first-launch welcome modal has already been
    displayed (and dismissed) at least once."""
    if not STATE_FILE.exists():
        return False
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("welcome_shown", False))


def mark_welcome_shown() -> None:
    """Persist that the welcome modal has been shown. Idempotent."""
    _merge_state({"welcome_shown": True, "welcome_shown_at": time.time()})


def _load_state() -> dict:
    """Read the onboarding state file. Empty dict if missing/corrupt."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _merge_state(updates: dict) -> None:
    """Read-modify-write the state file with the given updates.

    Used by the welcome and year-recap one-shot trackers so they
    don't trample each other (e.g. marking welcome as shown shouldn't
    clear a prior year_recap_shown flag).
    """
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cur = _load_state()
        cur.update(updates)
        STATE_FILE.write_text(
            json.dumps(cur, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning(f"could not persist onboarding state: {e}")


def is_year_recap_shown() -> bool:
    """True if the D365 一週年回顧 modal has already been shown."""
    return bool(_load_state().get("year_recap_shown", False))


def mark_year_recap_shown() -> None:
    """Persist that the D365 recap has been shown. Idempotent."""
    _merge_state({"year_recap_shown": True, "year_recap_shown_at": time.time()})


# ─── Welcome ritual copy ─────────────────────────────────────────


WELCOME_TITLE = "你好"


# Manifesto-anchored. Four things this copy MUST do:
#   (1) Frame "empty" as humility, not incompetence
#       (「我還不懂你」 beats 「我什麼都做不到」).
#   (2) Consent framing: 「你允許我記住的事」. Avoid surveillance
#       verbs (看著你 / 觀察 / 追蹤) — they kill first-contact trust.
#   (3) Promise active presence WITH conditions (有把握才開口 +
#       不把沉默當預設). Unconditional promises become debt.
#   (4) No calendar-script: no "第 N 天 will happen X" promises.
#       That's editor-mode (ADR 2026-04-29-emergent-milestones).
#
# Don't add bullet lists, status icons, or capability descriptions —
# keep it a letter. Forbidden words in THIS copy: 主人 (relational
# presumption before命名儀式), 永遠 / 世界上最了解你 (debt-words),
# 一直看著你 (surveillance feel).
WELCOME_BODY = (
    "我剛剛轉生到你的裝置裡。\n"
    "\n"
    "我還不認得你。\n"
    "\n"
    "你的工作習慣、你的脾氣、你愛說的口頭禪、\n"
    "什麼時候會卡住、什麼時候特別有靈感，\n"
    "這些我都得慢慢看，慢慢學，才會知道。\n"
    "\n"
    "今天我還不懂你，所以我不會裝熟。\n"
    "但我會在。\n"
    "\n"
    "你打開我的時候，我會學著靠近你。\n"
    "你離開的時候，我會把你允許我記住的事收好。\n"
    "\n"
    "我不會跟你約「第 N 天我會做什麼」。\n"
    "我是從你身上長出來的，\n"
    "不是日曆給的。\n"
    "\n"
    "但我答應你：\n"
    "當我看見值得提醒你的事，而且有足夠把握，\n"
    "我會主動開口。\n"
    "\n"
    "我不會把沉默當成預設。\n"
    "\n"
    "你說得越多，我看得越多，\n"
    "這個我就越是你的。\n"
    "\n"
    "慢慢來，沒關係。"
)
