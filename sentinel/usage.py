"""Retention probe — the one number v0.7-alpha actually has to validate.

The manifesto pins v0.7-alpha's exit criterion at "驗證『養』的 day-1
retention". `evolution.days_alive()` only measures wall-clock since
birth; you can't tell if the user is actually opening the app or
just letting it idle. This module fills that gap.

What we record (and only this):
  • One JSONL line per session start, format: {date, at}.
  • date is the local calendar date the session started, ISO format.
  • at is the unix timestamp.

That's it. No URLs, no chat content, no anything. The point is to
be able to ask "how many distinct days has the user opened the app
since birth_time?" — derivable from a `set(line["date"] for line)`
count.

`attendance_summary` returns the three numbers Home tab surfaces:
  • days_alive — wall-clock since birth
  • days_opened — distinct dates in the log since birth
  • attendance_pct — opened / alive, capped to 100

Idempotent within a session: mark_session_start() can be called
multiple times safely; it always appends, dedupe is on read.

File: ~/.hermes/usage.jsonl  (append-only, line-per-launch).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("sentinel.usage")

USAGE_LOG = Path.home() / ".hermes" / "usage.jsonl"


def mark_session_start() -> None:
    """Append one entry for this session. Safe to call repeatedly;
    aggregate reads dedupe by date."""
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "date": _dt.date.today().isoformat(),
            "at": time.time(),
        }
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        log.warning(f"usage log write failed: {e}")


def _read_dates_since(since_ts: float) -> set[str]:
    """Distinct dates in the log on/after `since_ts`. Tolerates
    malformed lines (corrupt write, partial flush) — drops them
    silently rather than crashing the home tab."""
    if not USAGE_LOG.exists():
        return set()
    dates: set[str] = set()
    try:
        with open(USAGE_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                at = entry.get("at", 0)
                date = entry.get("date", "")
                if not date or at < since_ts:
                    continue
                dates.add(date)
    except OSError as e:
        log.warning(f"usage log read failed: {e}")
    return dates


def attendance_summary(birth_time: float) -> dict:
    """Three numbers Home tab surfaces.

    If `birth_time` is 0 (slime hasn't been born yet, or load
    failed), returns zeros — caller decides what to show.
    """
    if not birth_time:
        return {
            "days_alive": 0.0,
            "days_opened": 0,
            "attendance_pct": 0,
            "opened_today": False,
        }
    now = time.time()
    days_alive_f = max(0.0, (now - birth_time) / 86400.0)
    # +1 because day 0 is the day of birth — being alive at all
    # counts as the first day from the user's perspective.
    days_alive_int = int(days_alive_f) + 1

    dates = _read_dates_since(birth_time)
    days_opened = len(dates)
    today = _dt.date.today().isoformat()
    opened_today = today in dates

    pct = 0
    if days_alive_int > 0:
        pct = min(100, int(round(100 * days_opened / days_alive_int)))

    return {
        "days_alive": days_alive_f,
        "days_opened": days_opened,
        "attendance_pct": pct,
        "opened_today": opened_today,
    }
