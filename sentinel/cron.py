"""Cron tick — the single source of truth for periodic checks.

Both `daemon.monitor_loop` (--no-gui mode) and `gui.py`'s observation
thread (start.bat / GUI mode) need to fire the same per-tick checks:

  - emergent_self_mark consultation (≤1/24h via internal cap)
  - loneliness arc check          (≤1/30d via internal cap)

Before this module existed the two loops each had their own copy of
the calls. We were bitten 4 times in 24 hours by the parallel-loop
class of bug:

  - PR #99 fixed cron-reset in daemon.py only. GUI still broken.
  - PR #107 killed push-spam in gui.py only. Daemon was already fine.
  - PR #108 wired cron into GUI. Daemon kept its independent copy.
  - The whole 'emergent never fired in production' epic of 2026-05-01
    was rooted in 'daemon.monitor_loop never runs because start.bat
    goes GUI', meaning all of PR #99's work landed in dead code.

This module collapses the duplication: one function, two callers,
no drift possible. If a new periodic check needs to ride alongside
emergent / loneliness, add it here; both loops pick it up
automatically.

Caller responsibilities (NOT in this module):
  - Manage the timer (when to fire). Each loop's cadence is its
    own — daemon uses last_idle_report, GUI uses last_cron. We
    don't re-implement timing here because the two loops have
    different surrounding work (Telegram idle_report only fires
    in daemon's case; GUI's tick is purely cron).
  - Surface failures to the user. tick() catches per-check
    exceptions internally so one broken check can't kill the
    other; but if both go quiet for days, that's a real outage
    visible in preflight.cron_consultations FAIL.

Rate caps (24h emergent / 30d loneliness) live in the called
functions themselves. tick() being called too often is harmless;
called too rarely shows up in preflight.
"""
from __future__ import annotations

import logging

log = logging.getLogger("sentinel.cron")


def tick() -> None:
    """One cron tick — fire emergent_self_mark + loneliness arc checks.

    Each underlying call is isolated in its own try/except so a bug
    or LLM failure in one doesn't take down the other. Both functions
    have their own internal rate caps; we just give them opportunities.
    """
    try:
        from sentinel.emergent_self_mark import record_emergent_moment_if_due
        record_emergent_moment_if_due()
    except Exception as e:
        log.warning(f"emergent self-mark check error: {e}")

    try:
        from sentinel import identity
        identity.record_loneliness_arc_if_due()
    except Exception as e:
        log.warning(f"loneliness arc check error: {e}")
