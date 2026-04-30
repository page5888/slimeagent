"""Aggregate recent window-activity for the chat prompt.

Background: `sentinel/activity_tracker.py` already records window
switches into `~/.hermes/sentinel_activity.jsonl` every time the
master's foreground app/window changes. `sentinel/learner.py` and
`sentinel/reflection/generator.py` already feed that data into LLMs
on their own cycles. But `sentinel/chat.py`'s system prompt — the
one in front of every reply the slime gives the master — never
saw it.

Result reported by a real master after 2 weeks of v0.7.x daemon use:
「他也不會看我的電腦我在做什麼。」 The data was there. The chat
path just wasn't reading it.

This module is the smallest fix: a pure read + format helper that
chat.py can splice into its system prompt as one block. No new
sensors, no new permissions, no VLM call (that's a separate, more
invasive feature gated separately when/if we want it).

What this is NOT:
  - Screenshot-based vision. The system-prompt rule
    「你不能直接看主人的螢幕（除非觀察區塊裡有截圖摘要）」still
    holds; this module never looks at pixels.
  - A new approval surface. We're not proposing actions; we're
    enriching context the slime already has under-the-hood access to.
  - Privacy expansion. The same data file is already read by
    learner.py + reflection. Bringing it into chat does not surface
    anything new to the cloud LLM that wasn't already going.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.recent_activity")

ACTIVITY_LOG = Path.home() / ".hermes" / "sentinel_activity.jsonl"

# Default window — 30 min is short enough that "最近你都..." reads as
# present-tense, long enough to capture a coherent task across a few
# context switches.
DEFAULT_WINDOW_MINUTES = 30

# Cap on processes shown so a "tab-hopping" hour doesn't produce
# a 20-line dump in the system prompt.
MAX_PROCESSES_SHOWN = 5

# Per-process cap on representative window titles. Few enough that
# the slime can naturally mention them without listing.
MAX_TITLES_PER_PROCESS = 3

# Defensive truncation of a single title — file paths can run long,
# git commit messages can run long, etc.
TITLE_MAX_CHARS = 80


def _read_recent_rows(now: float, window_seconds: float,
                      path: Path = ACTIVITY_LOG) -> list[dict]:
    """Return rows whose `time` is within the last `window_seconds`.

    Tolerates corrupt / partial lines. Linear scan from end of file
    isn't worth implementing — the file's size at typical use is
    well under a megabyte for a 30-min window's worth of rows.
    """
    if not path.exists() or window_seconds <= 0:
        return []
    cutoff = now - window_seconds
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
                ts = row.get("time")
                if not isinstance(ts, (int, float)):
                    continue
                if ts >= cutoff:
                    out.append(row)
    except OSError as e:
        log.debug("could not read activity log: %s", e)
    return out


def _aggregate(rows: list[dict]) -> list[tuple[str, int, list[str]]]:
    """Aggregate rows by process. Returns sorted [(proc, total_minutes,
    [titles]), ...] ordered by total_minutes desc.

    `titles` is unique-preserving (dict-style) and capped at
    MAX_TITLES_PER_PROCESS.
    """
    seconds_by_proc: dict[str, float] = defaultdict(float)
    titles_by_proc: dict[str, list[str]] = defaultdict(list)
    titles_seen: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        proc = (row.get("process") or "?").strip() or "?"
        try:
            dur = float(row.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0
        seconds_by_proc[proc] += dur

        title = (row.get("title") or "").strip()
        if title and title not in titles_seen[proc]:
            titles_seen[proc].add(title)
            if len(titles_by_proc[proc]) < MAX_TITLES_PER_PROCESS:
                titles_by_proc[proc].append(title[:TITLE_MAX_CHARS])

    ranked = sorted(
        seconds_by_proc.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )[:MAX_PROCESSES_SHOWN]

    return [
        (proc, max(1, int(round(secs / 60))), titles_by_proc[proc])
        for proc, secs in ranked
    ]


def build_block(now: Optional[float] = None,
                window_minutes: int = DEFAULT_WINDOW_MINUTES,
                path: Path = ACTIVITY_LOG) -> str:
    """Return the chat-prompt block for recent activity, or "".

    Empty string when there's nothing to show — chat.py can splice
    the result directly without conditional rendering.

    Format:
        === 主人最近 N 分鐘的視窗活動 ===
        - VS Code (24 分鐘) — sentinel/chat.py · tests/test_chat.py
        - Chrome (5 分鐘) — Stack Overflow / Python docs

        (自然提到，不要列舉；主人問的時候才具體)
    """
    if now is None:
        now = time.time()
    rows = _read_recent_rows(now, window_minutes * 60, path=path)
    aggregated = _aggregate(rows)
    if not aggregated:
        return ""

    lines = [f"=== 主人最近 {window_minutes} 分鐘的視窗活動 ==="]
    for proc, mins, titles in aggregated:
        if titles:
            joined = " · ".join(titles)
            lines.append(f"- {proc} ({mins} 分鐘) — {joined}")
        else:
            lines.append(f"- {proc} ({mins} 分鐘)")
    lines.append("")
    lines.append("(自然提到，不要列舉；主人問的時候才具體)")

    return "\n".join(lines)
