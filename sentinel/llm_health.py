"""Daily LLM-rate-error visibility for the daemon.

The slime hits multiple cloud providers, each with their own quotas
and rate limits (Gemini free tier is 20 req/day/model — easy to
saturate when daily card + distill cycles + emergent self-mark all
share the same key). Today the only signal is `log.warning` lines
buried in `~/.hermes/sentinel.log`, which the master only sees if
they happen to tail the file.

This module is the smallest visibility layer that doesn't grow into
"yet another circuit breaker": append a structured JSONL row when a
provider call fails with a rate-class error, and offer a today-only
summary readable from a one-shot CLI (`scripts/llm_health_today.py`)
or by future GUI integration.

Deliberately *not* in scope:
  - Auto-disabling providers that hit limits (the existing fallback
    chain in `llm._try_cloud` already handles failover; we only
    surface what's happening).
  - Historical analytics across days (today is the actionable window;
    yesterday's quotas reset; week-long trends would mean designing
    a real metrics pipeline).
  - Predictive throttling (would require a model of each provider's
    quota window, which the providers themselves don't always
    publish in stable form).

Storage: append-only JSONL at `~/.hermes/llm_health.jsonl`. Each row
is small (~150 bytes); even a worst-case "every call rate-limits"
day at the existing call rate stays well under 1 MB. We don't rotate
because the read path filters by today's local date — yesterday's
rows are read-once-then-ignored.

Day boundary: **local** midnight, not UTC. Someone in GMT+8 working
at 23:30 cares whether they've burned today's quota, not whether the
UTC counter has flipped.
"""
from __future__ import annotations

import datetime
import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.llm_health")

LOG_PATH = Path.home() / ".hermes" / "llm_health.jsonl"


# ── Recording ───────────────────────────────────────────────────────


def record_rate_error(provider_name: str, model_name: str,
                      error_str: str) -> None:
    """Append a rate-error row. Fire-and-forget.

    The caller is the per-provider call site in `sentinel.llm` after
    `_is_rate_error(error_str)` returns True. We accept the error
    string rather than the exception object to keep this module free
    of provider SDK imports.

    Failures here are swallowed — health logging must never break a
    real LLM call's exception path.
    """
    row = {
        "ts": time.time(),
        "type": "rate_error",
        "provider": provider_name,
        "model": model_name,
        # Preserve the first 240 characters; full SDK errors include
        # a JSON dump of quota details that's useful to keep but not
        # at unbounded size.
        "error": str(error_str)[:240],
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        log.debug("could not write llm_health row: %s", e)


# ── Read / summarize ────────────────────────────────────────────────


def _today_local_bounds(now: float) -> tuple[float, float]:
    """[start_of_today_local, start_of_tomorrow_local) as epoch seconds."""
    dt = datetime.datetime.fromtimestamp(now)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _read_today_rows(now: float, path: Path = LOG_PATH) -> list[dict]:
    """Return today's parsed rows. Tolerates corrupt / partial lines.

    The whole file is scanned because we don't index — at the row
    rate this module sees, a linear scan stays cheap (a megabyte-class
    file parses in milliseconds). If the read path ever needs to be
    cheaper, the right move is rotation by day, not an index.
    """
    if not path.exists():
        return []
    t0, t1 = _today_local_bounds(now)
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
                if t0 <= ts < t1:
                    out.append(row)
    except OSError as e:
        log.debug("could not read llm_health log: %s", e)
    return out


def get_today_summary(now: Optional[float] = None,
                      path: Path = LOG_PATH) -> dict:
    """Return today's rate-error summary.

    Shape:
        {
            "total_rate_errors": int,
            "by_provider": {
                "Gemini": {
                    "count": int,
                    "models": {"gemini-2.5-flash": int, ...},
                },
                ...
            },
            "primary_blocked": bool,
            "primary_provider": str,   # display-only, from config
        }

    `primary_blocked` is True iff every model in
    `config.LLM_PROVIDERS[0]["models"]` shows at least one rate
    error today. That's the home-tab signal: the master's first-
    choice provider is fully exhausted and silent fallback is in
    play.
    """
    if now is None:
        now = time.time()

    rows = _read_today_rows(now, path=path)

    by_provider: dict[str, dict] = {}
    for row in rows:
        if row.get("type") != "rate_error":
            continue
        prov = str(row.get("provider", "?"))
        model = str(row.get("model", "?"))
        bucket = by_provider.setdefault(prov, {"count": 0, "models": {}})
        bucket["count"] += 1
        bucket["models"][model] = bucket["models"].get(model, 0) + 1

    # primary_blocked: pull primary provider from config and check
    # whether every one of its models has been rate-errored today.
    # We import lazily so unit tests can stub config.
    primary_provider = "?"
    primary_blocked = False
    try:
        from sentinel import config
        if config.LLM_PROVIDERS:
            p0 = config.LLM_PROVIDERS[0]
            primary_provider = str(p0.get("name", "?"))
            primary_models = list(p0.get("models", []) or [])
            if primary_models:
                hit = by_provider.get(primary_provider, {}).get("models", {})
                primary_blocked = all(m in hit for m in primary_models)
    except Exception as e:
        log.debug("could not check primary_blocked: %s", e)

    return {
        "total_rate_errors": sum(b["count"] for b in by_provider.values()),
        "by_provider": by_provider,
        "primary_blocked": primary_blocked,
        "primary_provider": primary_provider,
    }


# ── Idle-report integration ─────────────────────────────────────────


def compose_idle_warning(now: Optional[float] = None,
                         path: Path = LOG_PATH) -> Optional[str]:
    """Return a one-line warning to embed in daemon's idle report, or None.

    Surfaces the silent-fallback case: every model in the primary
    provider's list has hit a rate error today, so the daemon is
    quietly using fallback providers and the master has no signal
    unless they go look at sentinel.log.

    Returns:
        - A formatted string when `primary_blocked` is True.
        - None when no primary provider info is available, no rate
          errors today, or only some primary models are affected.

    Stateless on purpose. The daemon's idle report fires every
    IDLE_REPORT_INTERVAL (default 30 min, ~48 messages/day) — adding
    a line to messages that have something to say doesn't change the
    base rate, and keeping it stateless means the warning stops on
    its own when conditions change (e.g. master adds a new key, model
    quotas reset, a fallback ALSO starts blocking and primary is no
    longer the bottleneck).
    """
    summary = get_today_summary(now=now, path=path)
    if not summary["primary_blocked"]:
        return None
    prov = summary["primary_provider"]
    bucket = summary["by_provider"].get(prov, {})
    n_errors = bucket.get("count", 0)
    n_models = len(bucket.get("models", {}))
    return (
        f"⚠️ LLM: {prov} 今天 {n_models} 個 model 全踩過 rate error "
        f"（共 {n_errors} 次），daemon 正在靜默 fallback 到備援 provider。"
    )
