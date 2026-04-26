"""Reactive dispatcher — turn EventBus events into routine fires.

Lives between observation modules (which publish events to
sentinel.routines.events) and the routine engine (which knows how to
execute steps via the workflow engine).

Logic per event:
  1. Read all enabled routines whose trigger.kind is reactive.
  2. For each, check if the event matches the trigger's predicate
     (e.g. on_app_open's title_match against the event's title).
  3. Apply per-routine cooldown — skip if last fire was less than
     cooldown_seconds ago. Reactive events tend to burst (a file-
     change rename produces 3 events in 50ms; window switching
     across 5 apps in a row); without throttling the user gets
     hammered with duplicate fires.
  4. fire_routine() through the same workflow engine path used by
     the cron scheduler. Audit log records the event payload that
     triggered it.

This module subscribes to specific event types at import time
(register_reactive_triggers). Observation modules publish events
when they detect changes; we don't need to poll.

The on_idle trigger is special: there's no "you became idle" event
from the input layer. Instead, the cron scheduler periodically
checks idle duration and synthesizes EVENT_IDLE_REACHED when the
threshold is crossed. See scheduler.py _check_idle_routines().
"""
from __future__ import annotations

import fnmatch
import logging
import time
from typing import Optional

from sentinel.routines import events as _events
from sentinel.routines.storage import (
    Routine, list_routines, get_routine,
    DEFAULT_COOLDOWN_SECONDS,
    TRIGGER_ON_APP_OPEN, TRIGGER_ON_FILE_PATTERN, TRIGGER_ON_IDLE,
)

log = logging.getLogger("sentinel.routines.reactive")

REACTIVE_KINDS = {
    TRIGGER_ON_APP_OPEN, TRIGGER_ON_FILE_PATTERN, TRIGGER_ON_IDLE,
}


def _matches(routine: Routine, event: _events.Event) -> bool:
    """Decide whether `event` is what this reactive routine waits for."""
    trig = routine.trigger or {}
    kind = trig.get("kind")

    if kind == TRIGGER_ON_APP_OPEN:
        if event.event_type != _events.EVENT_APP_OPEN:
            return False
        needle = (trig.get("title_match") or "").lower()
        if not needle:
            return False
        title = (event.payload.get("title") or "").lower()
        process = (event.payload.get("process_name") or "").lower()
        # Match against either window title or process name — users
        # think in either ("VS Code") or ("Code.exe"); accept both.
        return needle in title or needle in process

    if kind == TRIGGER_ON_FILE_PATTERN:
        if event.event_type != _events.EVENT_FILE_CHANGE:
            return False
        pattern = trig.get("pattern") or ""
        if not pattern:
            return False
        path = event.payload.get("path") or ""
        # fnmatch handles the typical glob shapes users will write
        # ("*.log", "**/error*", "/abs/path/specific.txt").
        return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(
            path.replace("\\", "/"), pattern
        )

    if kind == TRIGGER_ON_IDLE:
        if event.event_type != _events.EVENT_IDLE_REACHED:
            return False
        target_minutes = int(trig.get("duration_minutes", 0) or 0)
        if target_minutes <= 0:
            return False
        # The synthesizing scheduler emits EVENT_IDLE_REACHED with
        # duration_minutes = the threshold actually crossed. Match if
        # the routine's threshold was met or exceeded.
        actual = int(event.payload.get("duration_minutes", 0) or 0)
        return actual >= target_minutes

    return False


def _on_cooldown(routine: Routine) -> bool:
    """True iff routine fired recently enough to skip this event."""
    if routine.last_fired_at is None:
        return False
    cooldown = (
        routine.cooldown_seconds
        if routine.cooldown_seconds is not None
        else DEFAULT_COOLDOWN_SECONDS
    )
    elapsed = time.time() - routine.last_fired_at
    return elapsed < cooldown


def _on_event(event: _events.Event) -> None:
    """Top-level event handler. Iterates enabled reactive routines,
    fires those that match + aren't on cooldown."""
    # Avoid importing fire_routine at module top — circular with
    # scheduler.py which imports from this module.
    from sentinel.routines.scheduler import fire_routine

    for routine in list_routines():
        if not routine.enabled:
            continue
        kind = (routine.trigger or {}).get("kind")
        if kind not in REACTIVE_KINDS:
            continue
        if not _matches(routine, event):
            continue
        # Re-fetch to get the freshest last_fired_at — cron loop or
        # parallel reactive event might have already fired this.
        latest = get_routine(routine.id)
        if latest is None or _on_cooldown(latest):
            continue
        log.info(
            f"reactive: routine {routine.id} ({routine.name}) "
            f"matched {event.event_type}"
        )
        try:
            fire_routine(latest)
        except Exception:
            log.exception(f"reactive fire of {routine.id} raised")


_registered = False


def register_reactive_triggers() -> None:
    """Subscribe the dispatcher to all reactive event types.

    Idempotent — guarded by a module flag so re-calling during
    development reloads doesn't stack subscribers.
    """
    global _registered
    if _registered:
        return
    _events.subscribe(_events.EVENT_APP_OPEN, _on_event)
    _events.subscribe(_events.EVENT_FILE_CHANGE, _on_event)
    _events.subscribe(_events.EVENT_IDLE_REACHED, _on_event)
    _registered = True
    log.info("reactive trigger dispatcher registered")
