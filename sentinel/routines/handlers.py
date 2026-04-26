"""Approval-queue handlers for routine.* actions.

When the detector (or a manual chat proposal) submits a routine
candidate, it lands in the approval queue as a `routine.create`
action. The user reviews the proposed name + trigger + steps on the
approval card and decides. **Only after approval** does the routine
actually get written to disk and start firing.

Handlers registered here:
  routine.create  — persist the routine + activate it
  routine.disable — pause an existing routine (kept on disk)
  routine.delete  — permanent removal

Why three actions instead of one with a "verb" field?
  - Each has a different policy (create needs payload validation;
    disable/delete just need a valid id).
  - LLM proposals stay simple — the detector only ever proposes
    create. disable/delete are manual user actions through chat
    or future GUI.
"""
from __future__ import annotations

import logging
from typing import Any

from sentinel.routines import storage as _store

log = logging.getLogger("sentinel.routines.handlers")

# Same allowlist used by the detector — keeps a routine from
# referencing actions whose realtime equivalents had stronger
# real-time-user-in-loop checks (notably voice.listen).
_SAFE_STEP_ACTIONS = {
    "surface.open_path", "surface.open_url",
    "surface.focus_window", "surface.list_windows",
    "surface.get_clipboard", "surface.set_clipboard",
    "surface.take_screenshot",
    "voice.speak",
    "chain.run",
}


# ── Policy fns ────────────────────────────────────────────────────


def _policy_routine_create(payload: dict) -> tuple[bool, list[dict]]:
    """Validate proposed routine shape before showing to user.

    Catches: missing fields, unknown action types, malformed
    triggers, oversized step lists. Findings include step-index
    context so the user can see exactly which part is suspect on
    the approval card.
    """
    findings: list[dict] = []
    name = (payload or {}).get("name", "")
    if not isinstance(name, str) or not name.strip():
        return False, [{"level": "error", "msg": "name required"}]
    if len(name) > 80:
        return False, [{"level": "error", "msg": "name too long (max 80)"}]

    trig = (payload or {}).get("trigger") or {}
    if not isinstance(trig, dict):
        return False, [{"level": "error", "msg": "trigger must be a dict"}]
    kind = trig.get("kind")
    if kind == _store.TRIGGER_DAILY_AT or kind == _store.TRIGGER_WEEKLY_AT:
        time_spec = trig.get("time", "")
        if not _valid_hh_mm(time_spec):
            return False, [{
                "level": "error",
                "msg": f"trigger.time must be HH:MM (got {time_spec!r})",
            }]
        if kind == _store.TRIGGER_WEEKLY_AT:
            days = trig.get("days") or []
            if not isinstance(days, list) or not days:
                return False, [{
                    "level": "error",
                    "msg": "weekly_at trigger needs non-empty days list",
                }]
            allowed = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
            bad = [d for d in days if str(d).lower() not in allowed]
            if bad:
                return False, [{
                    "level": "error",
                    "msg": f"unknown days {bad} (use mon/tue/.../sun)",
                }]
    elif kind == _store.TRIGGER_INTERVAL:
        every = trig.get("every_minutes")
        try:
            every = int(every)
        except (TypeError, ValueError):
            return False, [{
                "level": "error",
                "msg": "interval every_minutes must be int",
            }]
        if every < 5 or every > 24 * 60:
            return False, [{
                "level": "error",
                "msg": "interval must be 5..1440 minutes",
            }]
    else:
        return False, [{
            "level": "error",
            "msg": f"unknown trigger kind {kind!r}",
        }]

    steps = (payload or {}).get("steps") or []
    if not isinstance(steps, list) or not steps:
        return False, [{"level": "error", "msg": "steps must be non-empty"}]
    if len(steps) > 5:
        return False, [{"level": "error", "msg": "max 5 steps per routine"}]
    for idx, s in enumerate(steps):
        if not isinstance(s, dict):
            return False, [{
                "level": "error",
                "msg": f"step {idx}: must be a dict",
            }]
        at = s.get("action_type")
        if at not in _SAFE_STEP_ACTIONS:
            return False, [{
                "level": "error",
                "msg": (
                    f"step {idx}: action_type {at!r} not allowed in routines. "
                    f"Routines auto-fire when you may not be at the keyboard, "
                    f"so we restrict to actions safe to run unattended."
                ),
            }]
        if not isinstance(s.get("payload", {}), dict):
            return False, [{
                "level": "error",
                "msg": f"step {idx}: payload must be a dict",
            }]

    # Auto-proposed routines surface their evidence as a warning so
    # the user sees WHY the slime thought this was a routine before
    # approving. Manually-created routines (no auto_proposed flag)
    # don't need this.
    if (payload or {}).get("auto_proposed"):
        ev = (payload or {}).get("evidence", "").strip()
        if ev:
            findings.append({
                "level": "info",
                "msg": f"史萊姆觀察依據：{ev[:200]}",
            })
    findings.append({
        "level": "warn",
        "msg": (
            f"同意後這個 routine 會自動執行 — "
            f"{_render_trigger_zh(trig)}。"
            f"想關掉到「設定」分頁找這個 routine 停用。"
        ),
    })
    return True, findings


def _policy_routine_disable(payload: dict) -> tuple[bool, list[dict]]:
    rid = (payload or {}).get("id", "")
    if not isinstance(rid, str) or not rid.startswith("rou_"):
        return False, [{"level": "error", "msg": "id must be 'rou_*'"}]
    if _store.get_routine(rid) is None:
        return False, [{"level": "error", "msg": f"routine {rid} not found"}]
    return True, []


def _policy_routine_delete(payload: dict) -> tuple[bool, list[dict]]:
    rid = (payload or {}).get("id", "")
    if not isinstance(rid, str) or not rid.startswith("rou_"):
        return False, [{"level": "error", "msg": "id must be 'rou_*'"}]
    if _store.get_routine(rid) is None:
        return False, [{"level": "error", "msg": f"routine {rid} not found"}]
    return True, [{
        "level": "warn",
        "msg": "刪除是永久的；只想暫停的話用 routine.disable",
    }]


# ── Executors ─────────────────────────────────────────────────────


def _exec_routine_create(payload: dict) -> dict:
    """Persist the routine (already policy-checked) + activate it."""
    routine = _store.create_routine(
        name=payload["name"],
        trigger=payload["trigger"],
        steps=payload["steps"],
        enabled=True,
        evidence=payload.get("evidence", ""),
    )
    return {
        "ok": True,
        "routine_id": routine.id,
        "name": routine.name,
        "trigger_summary": _render_trigger_zh(routine.trigger),
    }


def _exec_routine_disable(payload: dict) -> dict:
    rid = payload["id"]
    ok = _store.disable_routine(rid)
    return {"ok": ok, "routine_id": rid}


def _exec_routine_delete(payload: dict) -> dict:
    rid = payload["id"]
    ok = _store.delete_routine(rid)
    return {"ok": ok, "routine_id": rid}


# ── Helpers ───────────────────────────────────────────────────────


def _valid_hh_mm(s: str) -> bool:
    if not isinstance(s, str):
        return False
    try:
        h, m = s.split(":")
        return 0 <= int(h) < 24 and 0 <= int(m) < 60
    except (ValueError, AttributeError):
        return False


def _render_trigger_zh(trig: dict) -> str:
    """Pretty-print the trigger for approval card / chat / audit."""
    kind = (trig or {}).get("kind")
    if kind == _store.TRIGGER_DAILY_AT:
        return f"每天 {trig.get('time', '?')} 觸發"
    if kind == _store.TRIGGER_WEEKLY_AT:
        days = "/".join(trig.get("days", []))
        return f"每週 {days} 的 {trig.get('time', '?')} 觸發"
    if kind == _store.TRIGGER_INTERVAL:
        return f"每 {trig.get('every_minutes', '?')} 分鐘觸發一次"
    return "未知觸發條件"


# ── Registration ──────────────────────────────────────────────────


_REGISTERED: list[tuple[str, Any, Any]] = [
    ("routine.create",  _policy_routine_create,  _exec_routine_create),
    ("routine.disable", _policy_routine_disable, _exec_routine_disable),
    ("routine.delete",  _policy_routine_delete,  _exec_routine_delete),
]


def register_all() -> None:
    """Wire routine actions into the approval queue. Safe to call
    repeatedly (each register_action_handler overwrites prior)."""
    from sentinel.growth import register_action_handler
    for action_type, policy, handler in _REGISTERED:
        register_action_handler(
            action_type=action_type,
            handler=handler,
            policy=policy,
        )
    log.info(f"routine action handlers registered ({len(_REGISTERED)})")
