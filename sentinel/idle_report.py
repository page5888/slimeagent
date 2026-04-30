"""Compose Telegram idle-report messages — only when there's news.

Background: daemon's monitor_loop fires the idle-report cycle every
IDLE_REPORT_INTERVAL (default 30 min). The original code sent a
"💤 *AI Slime 定期報告*\n系統正常。\n{snapshot}" Telegram message
**unconditionally** — i.e. 48 messages/day baseline, regardless of
whether anything was actually wrong. Real users described it as
"很認真但很煩": the alerts were accurate, but the heartbeat noise
drowned them out.

Per ADR 2026-04-30 (`docs/decisions/2026-04-30-impulse-mechanism-framing.md`)
護欄 C: "通道升級需要主人明示同意" — the master's notification
attention is a budget. The 30-min heartbeat was an implicit-consent
default that should never have shipped that way.

This module owns the **gating logic** for what goes out as a
Telegram idle-report message. Pure function in / pure string-or-None
out so it's testable without standing up the daemon.

Decision: send a message **only when there's a meaningful signal**.

  - snapshot.warnings non-empty   →  CPU/RAM/disk anomaly worth flagging
  - llm_warning is a string       →  primary LLM provider is fully
                                     blocked today (compose_idle_warning
                                     in sentinel.llm_health)
  - both                          →  send both, joined with blank line
  - neither                       →  return None, daemon stays silent

Out of scope:
  - The local-state cron checks (loneliness arc, emergent self-mark)
    still run every cycle — they update local state, never touch
    Telegram. Those live in daemon, not here.
  - The crash notification (line 225 in daemon) and the alert path
    on detected warnings (line 154) are unchanged. Telegram still
    fires for genuine signals; this module only stops the heartbeat.
  - User-configurable cadence. Not adding a `enable_heartbeat` knob —
    heartbeat-style notifications are universally bad UX and providing
    a setting would sanction the bad default.
"""
from __future__ import annotations

from typing import Optional


# Format roughly mirrors the existing alert path
# (sentinel.daemon line 154):
#     "🟡 *AI Slime*\n<message>"
# so a master's Telegram chat reads consistently across signals.
_SNAPSHOT_HEADER = "🟡 *AI Slime*"


def compose_message(warnings: list,
                    snapshot_summary: str,
                    llm_warning: Optional[str]) -> Optional[str]:
    """Return the message to send, or None when there's nothing to say.

    Args:
        warnings: snapshot.warnings list (truthy = real anomaly).
            We only check truthiness; content is rendered via
            `snapshot_summary`.
        snapshot_summary: pre-rendered snapshot text (the daemon
            calls `snapshot.summary()` and passes the result here).
        llm_warning: output of `sentinel.llm_health.compose_idle_warning`
            (a one-liner with its own ⚠️ prefix), or None.

    Returns:
        A markdown-formatted message ready to pass to bot_send_fn,
        or None if both inputs are quiet.
    """
    pieces: list[str] = []
    if warnings:
        pieces.append(f"{_SNAPSHOT_HEADER}\n{snapshot_summary}")
    if llm_warning:
        pieces.append(llm_warning)
    if not pieces:
        return None
    # Blank line between sections so they read as separate signals
    # rather than a wall of text.
    return "\n\n".join(pieces)
