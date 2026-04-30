"""Structured consultation log for emergent self-mark decisions.

PR #81 added the daemon-side LLM consultation that decides whether
today is worth marking. The decision flow has multiple terminating
states (mark / refuse / parse_fail / unsafe / llm_none) but only
the `log.info` / `log.debug` lines in ~/.hermes/sentinel.log carry
that signal — mixed in with thousands of other lines, hard to
aggregate.

This module is the smallest persistent observability layer:

  - `record_consultation(outcome, reason)` — append a JSONL row at
    `~/.hermes/emergent_self_mark_log.jsonl`. Fire-and-forget; failures
    swallowed so health logging can never break the real flow.
  - `summarize_recent(days)` — read back, count by outcome, compute
    rejection rate. The numbers feed `scripts/check_b_preconditions.py`
    (the runnable encoding of ADR 2026-04-30's "開工訊號") and any
    future GUI that wants to surface "your slime asked itself N times
    this month, marked X times".

Mirrors `llm_health.py`'s pattern (separate module, JSONL append,
local-day filtering with explicit windows). Volume is tiny — at
most one row per day — so the file stays small forever; no rotation.

Out of scope (deliberately):
  - Storing the headline / detail of recorded marks (those already
    live in identity.memorable_moments — duplicating would let the
    two go out of sync)
  - Per-prompt-version tagging (we'd need a prompt registry first)
  - Cross-machine aggregation
"""
from __future__ import annotations

import datetime
import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.emergent_log")

LOG_PATH = Path.home() / ".hermes" / "emergent_self_mark_log.jsonl"

# Allowed outcome values. Keep this enum-like rather than free-form
# strings so the call sites can't drift to typos that would silently
# fall outside the summarizer's buckets.
OUTCOME_MARK = "mark"
OUTCOME_REFUSE = "refuse"
OUTCOME_PARSE_FAIL = "parse_fail"
OUTCOME_UNSAFE = "unsafe"
OUTCOME_LLM_NONE = "llm_none"
OUTCOME_EMPTY_HEADLINE = "empty_headline"

VALID_OUTCOMES = frozenset({
    OUTCOME_MARK,
    OUTCOME_REFUSE,
    OUTCOME_PARSE_FAIL,
    OUTCOME_UNSAFE,
    OUTCOME_LLM_NONE,
    OUTCOME_EMPTY_HEADLINE,
})


def record_consultation(outcome: str, reason: str = "") -> None:
    """Append a row describing a single LLM consultation outcome.

    Fire-and-forget. Validates `outcome` against `VALID_OUTCOMES` and
    drops invalid values to a debug log rather than crashing — the
    invariant is that this never breaks the real decision flow.
    """
    if outcome not in VALID_OUTCOMES:
        log.debug("invalid emergent_log outcome %r, dropping", outcome)
        return
    row = {
        "ts": time.time(),
        "outcome": outcome,
        "reason": str(reason)[:240],
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        log.debug("could not write emergent_log row: %s", e)


def _read_recent_rows(days: int, now: float,
                      path: Path = LOG_PATH) -> list[dict]:
    """Return rows whose ts falls within the last `days` (local-day window).

    Tolerates corrupt / partial lines. Linear scan — at the row rate
    this module sees (≤1/day), even a year-long file is trivial.
    """
    if not path.exists() or days <= 0:
        return []
    dt_now = datetime.datetime.fromtimestamp(now)
    cutoff = (dt_now - datetime.timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).timestamp()
    out: list[dict] = []
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
                ts = row.get("ts")
                if not isinstance(ts, (int, float)):
                    continue
                if ts >= cutoff:
                    out.append(row)
    except OSError as e:
        log.debug("could not read emergent_log: %s", e)
    return out


def summarize_recent(days: int = 30, now: Optional[float] = None,
                     path: Path = LOG_PATH) -> dict:
    """Aggregate the last `days` of consultations.

    Returns:
        {
            "window_days": int,
            "total_consultations": int,
            "by_outcome": {"mark": int, "refuse": int, ...},
            "rejection_rate": float,   # (refuse + parse_fail + unsafe + llm_none + empty_headline) / total
            "mark_count": int,         # short-cut for ADR (b) condition 1
        }

    `rejection_rate` = 1 - (mark_count / total_consultations).
    "Refuse" in the colloquial sense of the ADR includes anything that
    didn't end in a recorded mark — failures and explicit `mark=false`
    both count as the slime not actually marking the day.

    Empty log returns total=0, rejection_rate=0.0 (no division), so
    callers can distinguish "no data yet" from "100% refusal".
    """
    if now is None:
        now = time.time()
    rows = _read_recent_rows(days, now, path=path)

    by_outcome: dict[str, int] = {o: 0 for o in VALID_OUTCOMES}
    for row in rows:
        outcome = str(row.get("outcome", ""))
        if outcome in by_outcome:
            by_outcome[outcome] += 1

    total = sum(by_outcome.values())
    mark_count = by_outcome[OUTCOME_MARK]
    rejection_rate = (1.0 - mark_count / total) if total > 0 else 0.0

    return {
        "window_days": days,
        "total_consultations": total,
        "by_outcome": by_outcome,
        "rejection_rate": rejection_rate,
        "mark_count": mark_count,
    }
