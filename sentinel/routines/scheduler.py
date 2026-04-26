"""Routine scheduler — daemon thread that fires due routines.

Pattern: wake up every CHECK_INTERVAL seconds, scan all enabled
routines, fire any whose trigger condition is met. Each fire is
single-shot — if the user's machine is asleep at the trigger time,
the routine doesn't fire retroactively (we don't want to surprise
them with a backlog of "missed" actions when they wake up).

Trigger kinds (matched in _is_due):
  daily_at  : fires once per day at HH:MM (local time). Tracked via
              last_fired_at being on a different calendar day.
  weekly_at : fires at HH:MM on specified day(s) of week.
  interval  : fires every N minutes regardless of clock time.

The scheduler **never** invokes side effects directly. It calls
fire_routine() which translates the routine's steps into a
WorkflowEngine run — so checkpointing, retry, and audit get reused
for free, and the side effects go through the same Surface action
handlers that are already approval-gated for one-off chat use.

Threading:
  - One module-level daemon Thread, started by start_scheduler().
  - stop_scheduler() flips a flag; the loop exits at its next wake.
  - Reentrant lock around storage reads so a routine being saved
    mid-scan doesn't trip a partial JSON read.

Subtle design: routine fires on the same Python interpreter as the
GUI, so a long-running step (slow VLM call etc.) blocks the
scheduler from checking other routines until it returns. For v1
this is fine — we don't expect overlapping triggers within a
single minute. If we ever do, run each fire on its own short-lived
thread.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Optional

from sentinel.routines.storage import (
    Routine, list_routines, get_routine, record_fire,
    TRIGGER_DAILY_AT, TRIGGER_WEEKLY_AT, TRIGGER_INTERVAL,
)

log = logging.getLogger("sentinel.routines.scheduler")

# How often the loop wakes up to check trigger conditions. 60 s is
# fine for daily / weekly_at since we only need minute-level
# resolution. Interval routines smaller than 60 s just round up to
# the next check.
CHECK_INTERVAL_SECONDS = 60

# Pattern detector cadence. Once per 24h is plenty — detector reads
# accumulated activity logs that don't change minute-to-minute, and
# we don't want to ping the LLM every loop tick.
DETECTOR_INTERVAL_SECONDS = 24 * 60 * 60

# Reflection cadence (Phase J). Once per 7 days catches stale routines
# / chronic skip-rate problems without nagging the user with weekly
# "look at my stats" notifications.
REFLECTION_INTERVAL_SECONDS = 7 * 24 * 60 * 60

_scheduler_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_storage_lock = threading.RLock()
_last_detector_run = 0.0
_last_reflection_run = 0.0


# ── Trigger evaluation ────────────────────────────────────────────


def _is_due(routine: Routine, now: datetime) -> bool:
    """Decide whether this routine should fire NOW.

    `now` is provided rather than read inside so tests / dry-run code
    can pass a fixed time without monkey-patching datetime.
    """
    trig = routine.trigger or {}
    kind = trig.get("kind")
    if not routine.enabled:
        return False

    if kind == TRIGGER_DAILY_AT:
        target = trig.get("time", "")
        return _matches_time_today(routine, now, target)

    if kind == TRIGGER_WEEKLY_AT:
        days = [d.lower() for d in trig.get("days", []) or []]
        weekday = now.strftime("%a").lower()  # mon, tue, …
        if weekday not in days:
            return False
        target = trig.get("time", "")
        return _matches_time_today(routine, now, target)

    if kind == TRIGGER_INTERVAL:
        every = int(trig.get("every_minutes", 0) or 0)
        if every <= 0:
            return False
        if routine.last_fired_at is None:
            return True
        elapsed_minutes = (now.timestamp() - routine.last_fired_at) / 60.0
        return elapsed_minutes >= every

    return False


def _matches_time_today(routine: Routine, now: datetime, target: str) -> bool:
    """For daily_at / weekly_at: HH:MM must match current time AND
    the routine must not have already fired today.

    "Match" is HH:MM equality at minute resolution — two consecutive
    scans hitting the same minute won't both fire because last_fired_at
    moves the boundary. The scheduler runs at 60s cadence so we always
    have one chance per minute to catch the trigger.
    """
    try:
        hour, minute = target.split(":")
        target_h, target_m = int(hour), int(minute)
    except (ValueError, AttributeError):
        log.warning(f"routine {routine.id}: bad time spec {target!r}")
        return False
    if now.hour != target_h or now.minute != target_m:
        return False
    # Already fired today? Calendar-date comparison so a 9:00 routine
    # that fired this morning doesn't fire again at 9:00:30.
    if routine.last_fired_at is not None:
        last = datetime.fromtimestamp(routine.last_fired_at)
        if last.date() == now.date():
            return False
    return True


# ── Firing ────────────────────────────────────────────────────────


def _deps_satisfied(routine: Routine) -> tuple[bool, str]:
    """Phase K — verify every routine in `depends_on` has fired
    successfully within `depends_on_window_minutes`.

    Returns (ok, reason). ok=False means at least one dep is
    unsatisfied — caller logs + skips. Empty depends_on always
    returns (True, "").

    The check is "fire" event in the audit log only — we deliberately
    don't accept "fire_failed" or judge skips, because the dep
    semantics are "the previous routine SUCCEEDED" not "the previous
    routine ran". Otherwise a chain like [A, B-needs-A] could B
    incorrectly when A failed and you actually want B to wait for
    a successful A.
    """
    deps = routine.depends_on or []
    if not deps:
        return True, ""
    window = (routine.depends_on_window_minutes or 60) * 60
    cutoff = time.time() - window
    audit = _read_routine_audit()
    for dep_id in deps:
        last_success = None
        for e in reversed(audit):
            if e.get("id") == dep_id and e.get("event") == "fire":
                last_success = float(e.get("at", 0) or 0)
                break
        if last_success is None or last_success < cutoff:
            return False, (
                f"depends_on {dep_id} hasn't successfully fired in the "
                f"last {routine.depends_on_window_minutes} minutes"
            )
    return True, ""


def _read_routine_audit() -> list[dict]:
    """Read the routine audit log into a list of dicts. Shared with
    reflection.py — duplicated here as an inner helper to avoid a
    circular import (reflection imports scheduler for fire_routine).
    """
    from sentinel.routines.storage import AUDIT_LOG
    if not AUDIT_LOG.exists():
        return []
    out: list[dict] = []
    try:
        text = AUDIT_LOG.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def fire_routine(routine: Routine) -> dict:
    """Run a routine's steps via the workflow engine.

    Two pre-checks before workflow execution:
      Phase K — dependencies: every routine in `depends_on` must
        have fired successfully recently. Skip with explicit reason
        if any dep is unsatisfied.
      Phase H — judge: if `judge_prompt` is set, ask the LLM whether
        firing makes sense given current context. Skip on "skip".

    Each skip path records to audit + bumps last_fired_at (cooldown
    applies, no infinite re-checking) but DOESN'T run steps.

    Public for testing + for "fire now" buttons in future GUIs.
    Returns the workflow run summary (same shape as chain.run's
    handler result so existing chat formatters render it). When a
    skip path triggers, the return shape is {"ok": False, "skipped":
    True, "reason": "..."}.
    """
    log.info(f"firing routine {routine.id} ({routine.name})")

    # Phase K: dependency gate runs FIRST so a missing prereq doesn't
    # waste an LLM judge call. record_fire is imported at module-
    # level — DON'T re-import locally or Python turns it into a
    # function-scoped name and later usages UnboundLocalError.
    deps_ok, deps_reason = _deps_satisfied(routine)
    if not deps_ok:
        log.info(f"routine {routine.id} skipped — {deps_reason}")
        record_fire(routine, success=False, detail={
            "skipped_by_deps": True,
            "reason": deps_reason,
        })
        return {
            "ok": False,
            "skipped": True,
            "reason": deps_reason,
        }

    # Phase H: judge gate. Empty judge_prompt → unconditional fire.
    # record_fire comes from module-level import (not re-imported
    # locally — see Phase K note above for why).
    if (routine.judge_prompt or "").strip():
        from sentinel.routines.judge import evaluate
        decision = evaluate(
            routine_name=routine.name,
            judge_prompt=routine.judge_prompt,
            steps=routine.steps or [],
            trigger=routine.trigger or {},
        )
        if not decision.go:
            log.info(
                f"routine {routine.id} skipped by judge: {decision.reason}"
            )
            # Record as a fire event so cooldown applies. The audit
            # log and result both carry skipped=True so the user can
            # tell "judge said no" from "ran successfully".
            record_fire(routine, success=False, detail={
                "skipped_by_judge": True,
                "reason": decision.reason,
            })
            return {
                "ok": False,
                "skipped": True,
                "reason": decision.reason,
                "judge_raw": decision.raw[:300] if decision.raw else "",
            }

    from sentinel.workflow import Workflow, Step, WorkflowEngine
    from sentinel.growth.approval import _action_handlers

    wf_steps: list[Step] = []
    step_ids: list[tuple[str, str]] = []
    for idx, raw in enumerate(routine.steps or []):
        if not isinstance(raw, dict):
            continue
        action_type = raw.get("action_type", "")
        payload = raw.get("payload", {}) or {}
        title = raw.get("title") or action_type
        step_name = f"s{idx}_{action_type.replace('.', '_')}"
        step_ids.append((step_name, action_type))

        entry = _action_handlers.get(action_type)
        handler = entry["handler"] if entry else None

        def _step_fn(ctx, h=handler, at=action_type, sp=payload) -> dict:
            if h is None:
                raise ValueError(f"handler for {at} not registered")
            return h(sp)

        wf_steps.append(Step(
            name=step_name,
            fn=_step_fn,
            depends_on=[step_ids[idx - 1][0]] if idx > 0 else [],
            max_attempts=int(raw.get("max_attempts", 1) or 1),
        ))

    wf = Workflow(id=f"routine.{routine.id}", steps=wf_steps,
                  description=routine.name)
    try:
        run = WorkflowEngine().run(wf, inputs={"routine_id": routine.id})
    except Exception as e:
        log.exception("routine fire crashed")
        record_fire(routine, success=False, detail={"error": str(e)})
        return {"ok": False, "error": str(e)}

    summary = {
        "ok": run.status == "success",
        "run_id": run.run_id,
        "status": run.status,
        "steps": [
            {
                "action_type": at,
                "status": run.steps[name].status if name in run.steps else "missing",
                "error": run.steps[name].error if name in run.steps else None,
            }
            for name, at in step_ids
        ],
    }
    record_fire(routine, success=summary["ok"], detail=summary)
    return summary


# ── Loop ──────────────────────────────────────────────────────────


def _scheduler_loop() -> None:
    """The actual wake-and-check loop. Runs in a daemon thread."""
    log.info("routine scheduler started")
    # Initial small delay so the GUI's main thread can finish startup
    # before a routine fires (otherwise a daily_at trigger that
    # matches startup-time would race against UI init).
    if _stop_event.wait(timeout=5):
        return

    while not _stop_event.is_set():
        try:
            _scan_once()
        except Exception:
            log.exception("routine scheduler scan failed")
        if _stop_event.wait(timeout=CHECK_INTERVAL_SECONDS):
            break
    log.info("routine scheduler stopped")


def _scan_once() -> None:
    """One pass over all routines + occasional detector run."""
    now = datetime.now()
    with _storage_lock:
        routines = list_routines()
    for routine in routines:
        if not _is_due(routine, now):
            continue
        # Re-fetch from disk to get the freshest last_fired_at —
        # protects against double-fire if user manually fired the
        # same routine seconds before this scan.
        latest = get_routine(routine.id)
        if latest is None or not _is_due(latest, now):
            continue
        try:
            fire_routine(latest)
        except Exception:
            log.exception(f"routine {routine.id} fire raised")

    # Pattern detector — once per DETECTOR_INTERVAL_SECONDS. Wrapped
    # in its own try since LLM calls / log reads can fail and we
    # don't want detector hiccups to stall routine firing.
    global _last_detector_run
    if (time.time() - _last_detector_run) >= DETECTOR_INTERVAL_SECONDS:
        _last_detector_run = time.time()
        try:
            from sentinel.routines.detector import propose_via_detector
            queued = propose_via_detector()
            if queued:
                log.info(f"detector queued {len(queued)} routine proposals")
        except Exception:
            log.exception("routine detector failed")

    # Phase J reflection — once per REFLECTION_INTERVAL_SECONDS,
    # builds stats + queues self-suggestions through the approval
    # queue. The user sees stale-routine disables as approval cards
    # alongside everything else; "review skip rate" / "fail rate"
    # surface only when they explicitly ask in chat. This split keeps
    # passive reflection from spamming the queue while still letting
    # the slime act on the strongest signal (stale = clearly broken).
    global _last_reflection_run
    if (time.time() - _last_reflection_run) >= REFLECTION_INTERVAL_SECONDS:
        _last_reflection_run = time.time()
        try:
            from sentinel.routines.reflection import (
                reflect, queue_suggestions_as_proposals,
            )
            report = reflect()
            queued = queue_suggestions_as_proposals(report)
            if queued:
                log.info(
                    f"reflection queued {len(queued)} self-suggestions"
                )
        except Exception:
            log.exception("reflection pass failed")


def start_scheduler() -> None:
    """Spawn the daemon thread (idempotent — safe to call twice)."""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        name="sentinel.routines.scheduler",
        daemon=True,
    )
    _scheduler_thread.start()


def stop_scheduler(timeout: float = 2.0) -> None:
    """Signal the loop to exit. Mainly for tests + clean shutdown."""
    _stop_event.set()
    if _scheduler_thread is not None:
        _scheduler_thread.join(timeout=timeout)
