"""Routine persistence — one JSON file per routine in ~/.hermes/routines/.

Schema (per file):
{
  "id": "rou_a3f7b2",
  "name": "morning standup prep",         // human-readable, shown in UI
  "trigger": {                             // when does it fire?
    "kind": "daily_at" | "weekly_at" | "interval",
    "time": "09:00",                       // for daily_at / weekly_at
    "days": ["mon","tue", ...],            // for weekly_at
    "every_minutes": 60                    // for interval
  },
  "steps": [                               // same shape as chain.run
    {"action_type": "...", "payload": {...}, "title": "..."},
    ...
  ],
  "enabled": true,
  "created_at": <unix>,
  "last_fired_at": <unix> | null,
  "fire_count": <int>,
  "evidence": "<llm-generated rationale, if proposed by detector>",
  "approval_id": "<source proposal id, if any>"
}

Why JSON files instead of one big SQLite table?
  - Routines are sparse (we expect tens, not thousands)
  - Each is independently editable / inspectable in a text editor
  - Matches the existing aislime_evolution.json / pending_federation.json
    pattern in this project — one mental model for state on disk
  - File permission = simplest possible "user owns this"

Atomic writes via tmp + rename so a process kill mid-save can't
corrupt the file the scheduler will read next minute.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.routines.storage")

ROUTINES_DIR = Path.home() / ".hermes" / "routines"
AUDIT_LOG = ROUTINES_DIR / "routines.jsonl"


# Trigger kind constants (duplicated as strings so JSON shape stays stable
# even if we rename the python identifier later).
TRIGGER_DAILY_AT = "daily_at"
TRIGGER_WEEKLY_AT = "weekly_at"
TRIGGER_INTERVAL = "interval"
# Phase G — reactive triggers fire on environmental events, not clock.
# See sentinel/routines/events.py for the event bus that delivers them.
TRIGGER_ON_APP_OPEN = "on_app_open"        # window with matching title appears
TRIGGER_ON_FILE_PATTERN = "on_file_pattern"  # file matching glob changes
TRIGGER_ON_IDLE = "on_idle"                # user idle ≥ N minutes

# Defaults for the cooldown_seconds field, applied if a routine doesn't
# specify one. Reactive triggers can fire many times in quick succession
# (file-change burst, idle start/stop), so without throttling the user
# could see "did the same thing 30 times in 10 seconds".
DEFAULT_COOLDOWN_SECONDS = 300            # 5 min between fires


@dataclass
class Routine:
    """A recurring task. Persisted as JSON; loaded back via from_dict."""
    id: str
    name: str
    trigger: dict
    steps: list[dict]
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_fired_at: Optional[float] = None
    fire_count: int = 0
    evidence: str = ""             # set by detector if auto-proposed
    approval_id: str = ""          # source proposal id, if applicable
    # Phase G: minimum gap between successive fires for reactive
    # triggers. Cron-style triggers (daily_at etc.) ignore this since
    # their natural rate is already low. None = use DEFAULT_COOLDOWN_SECONDS.
    cooldown_seconds: Optional[int] = None
    # Phase H: optional natural-language judge. When set, after the
    # trigger condition matches but before the steps run, the slime
    # asks the LLM "given current context, should this routine fire
    # right now?" and only proceeds on "go". Empty/None means
    # unconditional fire (the Phase F/G behavior).
    #
    # Examples (what the user / detector might write):
    #   "今天主人有在電腦前嗎? 看最近 30 分鐘有沒有輸入活動。"
    #   "主人現在看起來在開會嗎? Zoom 視窗是不是 active?"
    #   "確認專案資料夾真的存在 D:\\srbow_bots 才執行。"
    #
    # The judge sees: routine name + steps + current context bus
    # render + recent memory. It outputs structured JSON {"decide":
    # "go"|"skip", "reason": "..."}. "skip" plus the reason gets
    # audit-logged so the user can see WHY the slime declined.
    judge_prompt: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Routine":
        # Accept extras (forward-compat with newer fields written by a
        # newer version) by filtering to known fields.
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid})


# ── Disk I/O ──────────────────────────────────────────────────────


def _ensure_dir() -> None:
    ROUTINES_DIR.mkdir(parents=True, exist_ok=True)


def _path_for(routine_id: str) -> Path:
    return ROUTINES_DIR / f"{routine_id}.json"


def _new_id() -> str:
    """Short, URL-safe, unique enough for local storage."""
    return "rou_" + secrets.token_hex(4)


def _atomic_write(path: Path, data: dict) -> None:
    """Tmp + rename so a crash mid-write can't leave a half-file.
    The scheduler reads these every minute; corruption would silently
    drop a routine until the user re-saved it."""
    _ensure_dir()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    tmp.replace(path)


def _audit(event: str, routine_id: str, extra: Optional[dict] = None) -> None:
    """Append-only event log. Never raises — logging shouldn't break flow."""
    try:
        _ensure_dir()
        entry = {
            "at": time.time(),
            "event": event,
            "id": routine_id,
            **(extra or {}),
        }
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── Public API ────────────────────────────────────────────────────


def create_routine(
    name: str,
    trigger: dict,
    steps: list[dict],
    *,
    enabled: bool = True,
    evidence: str = "",
    approval_id: str = "",
) -> Routine:
    """Create + persist a new routine. Caller is responsible for having
    validated the trigger / steps shape (validation lives in the action
    handler so it surfaces in the approval card before user approves)."""
    _ensure_dir()
    routine = Routine(
        id=_new_id(),
        name=name,
        trigger=trigger,
        steps=steps,
        enabled=enabled,
        evidence=evidence,
        approval_id=approval_id,
    )
    _atomic_write(_path_for(routine.id), asdict(routine))
    _audit("create", routine.id, {
        "name": name, "trigger_kind": trigger.get("kind"),
        "step_count": len(steps),
    })
    log.info(f"routine created: {routine.id} ({name})")
    return routine


def list_routines() -> list[Routine]:
    """All routines on disk, newest first."""
    _ensure_dir()
    out: list[Routine] = []
    for p in ROUTINES_DIR.glob("rou_*.json"):
        try:
            out.append(Routine.from_dict(
                json.loads(p.read_text(encoding="utf-8"))
            ))
        except Exception as e:
            log.warning(f"corrupt routine file {p.name}: {e}")
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


def get_routine(routine_id: str) -> Optional[Routine]:
    p = _path_for(routine_id)
    if not p.exists():
        return None
    try:
        return Routine.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning(f"could not load {routine_id}: {e}")
        return None


def _save(routine: Routine) -> None:
    _atomic_write(_path_for(routine.id), asdict(routine))


def enable_routine(routine_id: str) -> bool:
    r = get_routine(routine_id)
    if r is None:
        return False
    r.enabled = True
    _save(r)
    _audit("enable", routine_id)
    return True


def disable_routine(routine_id: str) -> bool:
    r = get_routine(routine_id)
    if r is None:
        return False
    r.enabled = False
    _save(r)
    _audit("disable", routine_id)
    return True


def delete_routine(routine_id: str) -> bool:
    """Permanent removal. Audit entry stays.

    Disable first via the GUI to keep a recoverable record; delete is
    for routines the user is sure they don't want back.
    """
    p = _path_for(routine_id)
    if not p.exists():
        return False
    try:
        p.unlink()
    except OSError as e:
        log.warning(f"delete {routine_id} failed: {e}")
        return False
    _audit("delete", routine_id)
    return True


def record_fire(
    routine: Routine,
    *,
    success: bool,
    detail: Optional[dict] = None,
) -> None:
    """Update last_fired_at + fire_count. Called by the scheduler after
    a routine runs (regardless of outcome — the audit log distinguishes
    success from failure for forensics)."""
    routine.last_fired_at = time.time()
    routine.fire_count += 1
    _save(routine)
    _audit(
        "fire" if success else "fire_failed",
        routine.id,
        detail or {},
    )
