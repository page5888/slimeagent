"""DailyCard data model + persistence.

Storage: one JSON file per date in ~/.hermes/daily_cards/, named
`YYYY-MM-DD.json`. Why not SQLite? Cards are small (1-2 KB), at most
365 per year, and the user might want to grep / cat / archive them
manually — same reasoning as routines/storage.py.

Schema (per file):
{
  "date": "2026-04-26",                    // ISO date string, the day the card REPRESENTS
  "generated_at": <unix>,                  // when the card was actually written
  "form_at_generation": "Slime+",          // slime's evolution form when it spoke
  "title_at_generation": "進化史萊姆",
  "observation": "...",                    // 區塊 1：what the slime saw
  "insight":     "...",                    // 區塊 2：what it thinks it means
  "micro_task":  "...",                    // 區塊 3：one small thing for today
  "feedback": {
      "state": "pending" | "accurate" | "partial" | "wrong",
      "answered_at": <unix> | null,
      "note": "..."                        // future: free-text reaction
  },
  "raw_metrics": {                         // numeric inputs for transparency
      "switch_count": 47,
      "top_apps": [...],
      "focus_blocks": [...],
      "claude_chats": <int>,
      ...
  }
}
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.reflection.daily_card")

CARDS_DIR = Path.home() / ".hermes" / "daily_cards"


# ── Feedback states ───────────────────────────────────────────────
# We use plain strings instead of an Enum so the JSON file stays
# trivially editable / readable. The tradeoff is no IDE auto-complete
# on values, but the set is tiny (4 options) and stable.

class Feedback:
    PENDING  = "pending"   # card shown but user hasn't reacted yet
    ACCURATE = "accurate"  # ✅ 準
    PARTIAL  = "partial"   # 🤔 有點像
    WRONG    = "wrong"     # ❌ 不對

    ALL = (PENDING, ACCURATE, PARTIAL, WRONG)


# ── Date helpers ──────────────────────────────────────────────────
# Local date (the user's wall clock). UTC would technically be more
# robust against DST shifts but the card is a personal ritual — local
# date is what the user expects.

def today_key() -> str:
    return date.today().isoformat()


def yesterday_key() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def date_key(d: date) -> str:
    return d.isoformat()


# ── DailyCard ─────────────────────────────────────────────────────


@dataclass
class DailyCard:
    """One day's reflection card from the slime."""

    date: str  # ISO date string for the day this card REPRESENTS
    generated_at: float = field(default_factory=time.time)
    form_at_generation: str = "Slime"
    title_at_generation: str = "初生史萊姆"
    observation: str = ""
    insight: str = ""
    micro_task: str = ""
    feedback_state: str = Feedback.PENDING
    feedback_answered_at: Optional[float] = None
    feedback_note: str = ""
    raw_metrics: dict = field(default_factory=dict)

    # ── Serialization ────────────────────────────────────────────
    # We keep feedback flat (state / answered_at / note as separate
    # top-level fields) at the dataclass level for ergonomic access,
    # but nest them under "feedback" in the JSON file for clearer
    # human reading. to_dict / from_dict bridge the two.

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "generated_at": self.generated_at,
            "form_at_generation": self.form_at_generation,
            "title_at_generation": self.title_at_generation,
            "observation": self.observation,
            "insight": self.insight,
            "micro_task": self.micro_task,
            "feedback": {
                "state": self.feedback_state,
                "answered_at": self.feedback_answered_at,
                "note": self.feedback_note,
            },
            "raw_metrics": self.raw_metrics,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DailyCard":
        fb = d.get("feedback") or {}
        return cls(
            date=d["date"],
            generated_at=d.get("generated_at", time.time()),
            form_at_generation=d.get("form_at_generation", "Slime"),
            title_at_generation=d.get("title_at_generation", "初生史萊姆"),
            observation=d.get("observation", ""),
            insight=d.get("insight", ""),
            micro_task=d.get("micro_task", ""),
            feedback_state=fb.get("state", Feedback.PENDING),
            feedback_answered_at=fb.get("answered_at"),
            feedback_note=fb.get("note", ""),
            raw_metrics=d.get("raw_metrics", {}),
        )

    # ── Convenience ──────────────────────────────────────────────

    @property
    def has_feedback(self) -> bool:
        return self.feedback_state != Feedback.PENDING

    def record_feedback(self, state: str, note: str = "") -> None:
        """Set the user's reaction. State must be one of Feedback.*."""
        if state not in Feedback.ALL:
            raise ValueError(f"unknown feedback state: {state}")
        self.feedback_state = state
        self.feedback_answered_at = time.time()
        if note:
            self.feedback_note = note


# ── Persistence ───────────────────────────────────────────────────


def _ensure_dir() -> None:
    CARDS_DIR.mkdir(parents=True, exist_ok=True)


def card_path(date_iso: str) -> Path:
    return CARDS_DIR / f"{date_iso}.json"


def load_card(date_iso: str) -> Optional[DailyCard]:
    """Return the card for `date_iso`, or None if it hasn't been
    generated yet. Corrupt files return None + log; we don't bubble
    so a single bad file doesn't crash the home tab."""
    path = card_path(date_iso)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return DailyCard.from_dict(raw)
    except (json.JSONDecodeError, KeyError, OSError) as e:
        log.warning("daily card load failed for %s: %s", date_iso, e)
        return None


def save_card(card: DailyCard) -> None:
    """Atomic-ish write: write to .tmp, rename. Prevents a half-flushed
    file from showing partial JSON on next read if the process is
    killed mid-write."""
    _ensure_dir()
    target = card_path(card.date)
    tmp = target.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(card.to_dict(), f, ensure_ascii=False, indent=2)
        tmp.replace(target)
    except OSError as e:
        log.error("daily card save failed for %s: %s", card.date, e)
        # Clean up partial tmp if the rename never happened.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def list_recent_cards(n: int = 7) -> list[DailyCard]:
    """Last N existing cards, newest first. Used by the "this week"
    review and (eventually) the home tab's history strip."""
    _ensure_dir()
    files = sorted(CARDS_DIR.glob("*.json"), reverse=True)
    out: list[DailyCard] = []
    for f in files[:n]:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                out.append(DailyCard.from_dict(json.load(fh)))
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    return out
