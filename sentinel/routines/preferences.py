"""Routine preferences — what the user has DISLIKED.

Phase I. The detector LLM proposes new routines based on observed
patterns. Without learning from rejections, it'd keep proposing
things the user already said no to — annoying. This module is the
slime's memory of "I tried this and master didn't want it",
formatted as negative examples for the detector's next run.

Three signal types, in increasing strength:

  rejected_proposal   user clicked 拒絕 on a routine.create card
                      (never even ran — wrong from the start)
  disabled_active     user disabled a previously-active routine
                      (initially OK, but turned out annoying)
  deleted_active      user deleted a previously-active routine
                      (strongest negative signal — get it out)

Storage: ~/.hermes/routines/preferences.jsonl — append-only.
Schema (per line):
  {
    "at": <unix>,
    "signal": "rejected_proposal" | "disabled_active" | "deleted_active",
    "summary": {
      "name": str,
      "trigger_kind": str,
      "trigger_specifics": {...},  // small subset of trigger payload
      "step_action_types": [str, ...],
      "judge_prompt": str | "",     // 200-char preview
    },
    "reason": str    // user's stated reason, or ""
  }

The summary fields are deliberately structural rather than verbatim —
we want to detect "user dislikes file-pattern triggers on download
folder" not "user dislikes the exact string he saw last time".
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


log = logging.getLogger("sentinel.routines.preferences")


PREFERENCES_FILE = Path.home() / ".hermes" / "routines" / "preferences.jsonl"

# Cap how many entries we surface to the detector. Keep newest
# because preferences shift; an old rejection might no longer
# reflect current behaviour. 30 is enough to learn from without
# blowing the detector's prompt budget.
DETECTOR_RECENT_LIMIT = 30


SIGNAL_REJECTED = "rejected_proposal"
SIGNAL_DISABLED = "disabled_active"
SIGNAL_DELETED = "deleted_active"


@dataclass
class Preference:
    """One stored negative signal."""
    at: float
    signal: str
    summary: dict
    reason: str = ""


# ── Helpers to build a Summary from various sources ──────────────


def _summary_from_routine_payload(payload: dict) -> dict:
    """Build the 'summary' field from a routine.create proposal payload
    (i.e. what the user saw on the rejected approval card)."""
    payload = payload or {}
    trigger = payload.get("trigger") or {}
    steps = payload.get("steps") or []
    return {
        "name": str(payload.get("name", ""))[:80],
        "trigger_kind": str(trigger.get("kind", "")),
        "trigger_specifics": _trigger_specifics(trigger),
        "step_action_types": [
            str(s.get("action_type", ""))
            for s in steps if isinstance(s, dict)
        ][:5],
        "judge_prompt": str(payload.get("judge_prompt", ""))[:200],
    }


def _summary_from_routine(routine) -> dict:
    """Build summary from a live Routine dataclass (used when the user
    disables / deletes an existing routine)."""
    return {
        "name": str(routine.name)[:80],
        "trigger_kind": str((routine.trigger or {}).get("kind", "")),
        "trigger_specifics": _trigger_specifics(routine.trigger or {}),
        "step_action_types": [
            str(s.get("action_type", ""))
            for s in (routine.steps or []) if isinstance(s, dict)
        ][:5],
        "judge_prompt": str(routine.judge_prompt or "")[:200],
    }


def _trigger_specifics(trigger: dict) -> dict:
    """Pull the 1-2 most identifying fields from a trigger so the
    summary captures shape without bloating the prompt."""
    if not isinstance(trigger, dict):
        return {}
    kind = trigger.get("kind", "")
    if kind in ("daily_at", "weekly_at"):
        return {"time": trigger.get("time", "")}
    if kind == "interval":
        return {"every_minutes": trigger.get("every_minutes")}
    if kind == "on_app_open":
        return {"title_match": str(trigger.get("title_match", ""))[:60]}
    if kind == "on_file_pattern":
        return {"pattern": str(trigger.get("pattern", ""))[:60]}
    if kind == "on_idle":
        return {"duration_minutes": trigger.get("duration_minutes")}
    return {}


# ── Disk I/O ──────────────────────────────────────────────────────


def _ensure_dir() -> None:
    PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)


def record(
    signal: str,
    summary: dict,
    reason: str = "",
) -> None:
    """Append one preference to the log. Never raises — learning
    signals are nice-to-have, never critical-path."""
    if signal not in (SIGNAL_REJECTED, SIGNAL_DISABLED, SIGNAL_DELETED):
        log.warning(f"unknown preference signal: {signal!r}; skipping")
        return
    pref = Preference(
        at=time.time(),
        signal=signal,
        summary=summary or {},
        reason=str(reason or "")[:300],
    )
    try:
        _ensure_dir()
        with open(PREFERENCES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(pref), ensure_ascii=False) + "\n")
        log.info(
            f"preference recorded: {signal} on "
            f"{summary.get('name', '?')[:40]}"
        )
    except OSError as e:
        log.warning(f"could not record preference: {e}")


def list_recent(limit: int = DETECTOR_RECENT_LIMIT) -> list[Preference]:
    """Read most recent preferences, newest first."""
    if not PREFERENCES_FILE.exists():
        return []
    out: list[Preference] = []
    try:
        text = PREFERENCES_FILE.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            out.append(Preference(
                at=float(d.get("at", 0)),
                signal=str(d.get("signal", "")),
                summary=d.get("summary") or {},
                reason=str(d.get("reason", "")),
            ))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    out.sort(key=lambda p: p.at, reverse=True)
    return out[:limit]


# ── Detector integration ─────────────────────────────────────────


def render_for_detector_prompt() -> str:
    """Format recent preferences as a prompt block telling the
    detector LLM what NOT to propose. Empty string if no signals.

    The block is structural so the LLM learns patterns rather than
    exact strings: trigger_kind + step actions + reason. So a user
    who disabled "morning open VS Code" routine will signal aversion
    to the (on_app_open, surface.open_path) pair, not just to that
    one specific path.
    """
    prefs = list_recent(DETECTOR_RECENT_LIMIT)
    if not prefs:
        return ""
    lines = [
        "=== 主人之前不喜歡的 routine（避免再提類似的） ==="
    ]
    severity = {
        SIGNAL_REJECTED: "拒絕了提案",
        SIGNAL_DISABLED: "用過後停用",
        SIGNAL_DELETED: "用過後刪掉（最不喜歡）",
    }
    for p in prefs:
        sev = severity.get(p.signal, p.signal)
        s = p.summary or {}
        name = s.get("name", "?")
        kind = s.get("trigger_kind", "?")
        specifics = s.get("trigger_specifics") or {}
        spec_str = ", ".join(f"{k}={v}" for k, v in specifics.items())
        steps = s.get("step_action_types") or []
        steps_str = " → ".join(steps) or "(no steps)"
        line = f"- [{sev}] 「{name}」 觸發={kind}({spec_str}) 步驟={steps_str}"
        if p.reason:
            line += f"  理由：{p.reason[:80]}"
        lines.append(line)
    lines.append(
        "\n判斷：如果你準備提的 routine 跟上面任何一個的觸發類型 + "
        "步驟組合很像，**降低 confidence 或不提**。主人說過不喜歡的，"
        "再提就是煩他。"
    )
    return "\n".join(lines)


# ── Approval queue hook ──────────────────────────────────────────


def _on_routine_rejection(approval, reason: str) -> None:
    """Approval-queue callback: when a routine.create proposal is
    rejected, record a preference. Other rejection types (skill_gen,
    self_mod, surface.* actions) aren't relevant to routine learning
    so we skip them.
    """
    try:
        if approval.kind != "action":
            return
        if approval.action_type != "routine.create":
            return
        record(
            signal=SIGNAL_REJECTED,
            summary=_summary_from_routine_payload(approval.payload),
            reason=reason,
        )
    except Exception as e:
        log.warning(f"preference rejection hook failed: {e}")


def register_with_approval_queue() -> None:
    """Wire _on_routine_rejection into the approval queue. Called
    once at daemon startup. Idempotent (register_on_reject filters
    duplicates)."""
    from sentinel.growth import register_on_reject
    register_on_reject(_on_routine_rejection)


# ── Daily card feedback (v0.7-alpha) ──────────────────────────────
# Card feedback is a separate signal channel from routine preferences:
# it learns what KIND of OBSERVATION the slime made that landed (or
# didn't), not what kind of routine the user accepts. We keep the two
# logs separate so future detector / generator tweaks don't have to
# discriminate by parsing one merged file.

CARD_FEEDBACK_FILE = Path.home() / ".hermes" / "routines" / "card_feedback.jsonl"


def record_card_feedback(
    date_iso: str,
    state: str,
    note: str = "",
    snapshot: dict | None = None,
) -> None:
    """Append a daily-card feedback signal.

    `state` should be one of `accurate` / `partial` / `wrong`. We
    don't validate too strictly — log everything, let downstream
    consumers filter.

    `snapshot` is an optional small dict of card content (e.g. truncated
    observation/insight text + raw_metric tags) so a future generator
    can tell "the user said WRONG when I commented on focus blocks"
    not just "the user said WRONG on 2026-04-26".
    """
    payload = {
        "at": time.time(),
        "date": date_iso,
        "state": state,
        "note": str(note or "")[:300],
        "snapshot": snapshot or {},
    }
    try:
        _ensure_dir()
        with open(CARD_FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        log.info("card feedback recorded: %s on %s", state, date_iso)
    except OSError as e:
        log.warning("could not record card feedback: %s", e)


def list_recent_card_feedback(limit: int = 30) -> list[dict]:
    """Most recent card feedback entries, newest first. Used by the
    next-iteration generator to avoid re-making the same kind of
    observation the user already rejected."""
    if not CARD_FEEDBACK_FILE.exists():
        return []
    out: list[dict] = []
    try:
        with open(CARD_FEEDBACK_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log.warning("could not read card feedback: %s", e)
        return []
    return list(reversed(out))[:limit]
