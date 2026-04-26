"""Reflection — the slime introspects on its own routine performance.

Phase J. Phases F-I gave the slime the ability to PROPOSE, GATE, and
LEARN-FROM-NO. Phase J adds the next-level capability: looking at
its own behavior over time and adjusting itself.

What gets reflected on:
  - Per-routine fire rate vs skip rate (Phase H judge declining a lot
    means the trigger is too eager OR the judge_prompt is too strict)
  - Per-routine failure rate (steps erroring out — broken handler /
    missing path / network)
  - Stale routines (haven't fired in a long time — trigger never
    matches, probably misconfigured)
  - Approval-queue noise (proposing too many that get rejected →
    detector is over-eager despite Phase I)

What reflection produces:
  - A natural-language summary the user can ask for in chat
  - A list of self-suggested adjustments, surfaced as approval-queue
    proposals (routine.disable for stale ones, free-text "建議"
    notes for tuning suggestions)

The slime never adjusts itself silently. Every reflection-driven
change goes through the same approval queue as anything else, so
the user remains in control. The only thing reflection does
autonomously is OBSERVE its own performance — turning that into
action requires a click.

Storage:
  No new files. Reads from:
    ~/.hermes/routines/*.json     (routine state + last_fired_at + fire_count)
    ~/.hermes/routines/routines.jsonl  (audit log: fire / fire_failed events)
    ~/.hermes/routines/preferences.jsonl (Phase I rejection signals)
    ~/.hermes/approvals/approvals.jsonl  (proposal acceptance rate)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


log = logging.getLogger("sentinel.routines.reflection")


# Thresholds for when reflection considers a routine "in trouble" or
# the system as a whole "noisy". Conservative enough that occasional
# random fluctuations don't trigger meta-suggestions; aggressive enough
# that real problems surface within a week or two.

STALE_DAYS = 30                  # routine hasn't fired in N days
HIGH_SKIP_RATE_THRESHOLD = 0.7   # >70% of fires were judge-skipped
HIGH_FAIL_RATE_THRESHOLD = 0.5   # >50% of fires errored
MIN_FIRES_FOR_RATE = 5           # need at least N fires for a rate
                                  # to be statistically meaningful


@dataclass
class RoutineStats:
    """Per-routine performance numbers, derived from disk + audit log."""
    routine_id: str
    name: str
    enabled: bool
    fire_count: int
    last_fired_at: Optional[float]
    # Decomposition of fires (from the audit log):
    success_count: int = 0
    fail_count: int = 0
    skipped_by_judge_count: int = 0

    # Derived properties for callers/UI.
    @property
    def total_fires(self) -> int:
        return self.success_count + self.fail_count + self.skipped_by_judge_count

    @property
    def skip_rate(self) -> float:
        return (self.skipped_by_judge_count / self.total_fires
                if self.total_fires else 0.0)

    @property
    def fail_rate(self) -> float:
        return self.fail_count / self.total_fires if self.total_fires else 0.0

    @property
    def days_since_last_fire(self) -> Optional[float]:
        if self.last_fired_at is None:
            return None
        return (time.time() - self.last_fired_at) / 86400.0


@dataclass
class ReflectionReport:
    """The full output of one reflection pass."""
    generated_at: float = field(default_factory=time.time)
    routine_stats: list[RoutineStats] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)
    # Suggestion shape: {kind, routine_id?, title, detail}
    # kinds: "disable_stale", "review_skip_rate", "review_fail_rate",
    #        "detector_noisy"


# ── Audit-log scanning ────────────────────────────────────────────


def _read_routine_audit() -> list[dict]:
    """Read the routines audit log into a list of dicts.

    The audit log is JSONL — one event per line. Each event has
    {at, event, id, ...}. We don't filter here; callers do their
    own filtering by event type.
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


def _stats_for_routine(routine, audit: list[dict]) -> RoutineStats:
    """Build RoutineStats by counting matching audit entries.

    A "fire" event means the workflow ran successfully. A
    "fire_failed" event with skipped_by_judge=True is a Phase H
    skip, distinguished from a fire_failed without that flag (which
    is a real step failure).
    """
    s = RoutineStats(
        routine_id=routine.id,
        name=routine.name,
        enabled=routine.enabled,
        fire_count=routine.fire_count,
        last_fired_at=routine.last_fired_at,
    )
    for e in audit:
        if e.get("id") != routine.id:
            continue
        action = e.get("event")
        if action == "fire":
            s.success_count += 1
        elif action == "fire_failed":
            if e.get("skipped_by_judge"):
                s.skipped_by_judge_count += 1
            else:
                s.fail_count += 1
    return s


# ── Suggestion generation ────────────────────────────────────────


def _suggest_for_routine(s: RoutineStats) -> list[dict]:
    """Per-routine suggestions based on stats."""
    out: list[dict] = []

    # Stale: enabled but hasn't fired in a long time. Trigger probably
    # never matches; the routine is dead weight.
    if s.enabled and s.days_since_last_fire is not None and \
            s.days_since_last_fire >= STALE_DAYS:
        out.append({
            "kind": "disable_stale",
            "routine_id": s.routine_id,
            "title": f"停用閒置 routine：{s.name}",
            "detail": (
                f"已啟用但 {int(s.days_since_last_fire)} 天沒觸發過。"
                f"觸發條件可能寫錯,或主人的習慣已經變了。"
                f"建議停用或檢視。"
            ),
        })

    # High skip rate: the judge keeps saying no. The trigger fires too
    # eagerly OR the judge_prompt is too strict.
    if s.total_fires >= MIN_FIRES_FOR_RATE and \
            s.skip_rate >= HIGH_SKIP_RATE_THRESHOLD:
        out.append({
            "kind": "review_skip_rate",
            "routine_id": s.routine_id,
            "title": f"檢視常被略過的 routine：{s.name}",
            "detail": (
                f"觸發後被 judge 略過率 {int(s.skip_rate * 100)}% "
                f"(觸發 {s.total_fires} 次,實際做 {s.success_count} 次)。"
                f"考慮放寬 judge_prompt 或縮窄 trigger。"
            ),
        })

    # High fail rate: steps keep erroring. Path doesn't exist /
    # handler not registered / network down.
    if s.total_fires >= MIN_FIRES_FOR_RATE and \
            s.fail_rate >= HIGH_FAIL_RATE_THRESHOLD:
        out.append({
            "kind": "review_fail_rate",
            "routine_id": s.routine_id,
            "title": f"檢視常失敗的 routine：{s.name}",
            "detail": (
                f"執行失敗率 {int(s.fail_rate * 100)}% "
                f"({s.fail_count} 次失敗 / {s.total_fires} 次觸發)。"
                f"檢查路徑或網路;考慮停用直到修好。"
            ),
        })
    return out


def _suggest_detector_noise() -> list[dict]:
    """System-level: is the detector too noisy?

    Looks at the rejection log + approvals audit to see what fraction
    of routine.create proposals get rejected. >70% rejection over the
    last N proposals suggests the detector is over-eager — worth
    flagging the user so they know we noticed.
    """
    try:
        from sentinel.routines.preferences import list_recent
        prefs = list_recent(50)
    except Exception:
        return []
    rejected = sum(
        1 for p in prefs if p.signal == "rejected_proposal"
    )
    if len(prefs) < 5:
        return []  # not enough data
    if rejected / max(1, len(prefs)) < 0.7:
        return []
    return [{
        "kind": "detector_noisy",
        "routine_id": None,
        "title": "我提案的 routine 你最近拒掉很多",
        "detail": (
            f"最近 {len(prefs)} 個訊號裡 {rejected} 個是「拒絕」"
            f"({int(rejected / len(prefs) * 100)}%)。"
            f"我會調整偵測標準,但如果你覺得我整個方向都錯,"
            f"也可以告訴我有興趣的範圍是什麼。"
        ),
    }]


# ── Public API ───────────────────────────────────────────────────


def reflect() -> ReflectionReport:
    """Run one reflection pass. Returns a report; doesn't act on it.

    The caller (scheduler / chat handler) decides whether to surface
    suggestions as approval-queue proposals or just show stats.
    """
    from sentinel.routines.storage import list_routines

    audit = _read_routine_audit()
    stats: list[RoutineStats] = []
    suggestions: list[dict] = []

    for r in list_routines():
        s = _stats_for_routine(r, audit)
        stats.append(s)
        suggestions.extend(_suggest_for_routine(s))

    suggestions.extend(_suggest_detector_noise())

    # Deduplicate suggestions by kind+routine_id so re-reflecting in
    # the same week doesn't double-propose the same thing.
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for sug in suggestions:
        key = (sug.get("kind"), sug.get("routine_id"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sug)

    return ReflectionReport(routine_stats=stats, suggestions=deduped)


def format_summary(report: ReflectionReport) -> str:
    """Natural-language digest for chat ("我最近 routine 表現如何")."""
    if not report.routine_stats:
        return "你還沒有任何 routine — 可以等我觀察一陣子,或在聊天裡叫我建一個。"

    enabled = [s for s in report.routine_stats if s.enabled]
    disabled = [s for s in report.routine_stats if not s.enabled]
    total_fires = sum(s.total_fires for s in report.routine_stats)
    total_success = sum(s.success_count for s in report.routine_stats)

    lines = [
        f"你有 {len(report.routine_stats)} 個 routine — 啟用 {len(enabled)} 個,停用 {len(disabled)} 個。",
        f"累計觸發 {total_fires} 次,成功跑完 {total_success} 次。",
    ]

    # Top 3 most-fired routines (showing the slime is being useful)
    by_success = sorted(
        report.routine_stats, key=lambda s: s.success_count, reverse=True
    )
    if by_success and by_success[0].success_count > 0:
        lines.append("\n常常派上用場的:")
        for s in by_success[:3]:
            if s.success_count == 0:
                break
            lines.append(
                f"  · {s.name} — 成功 {s.success_count} 次"
            )

    if report.suggestions:
        lines.append("\n我有 {n} 個自我調整建議:".format(
            n=len(report.suggestions),
        ))
        for sug in report.suggestions[:5]:
            lines.append(f"  · {sug['title']}")
            if sug.get("detail"):
                lines.append(f"    {sug['detail']}")
    else:
        lines.append("\n暫時沒有需要調整的東西。")
    return "\n".join(lines)


def queue_suggestions_as_proposals(report: ReflectionReport) -> list[str]:
    """Turn high-confidence suggestions into approval-queue proposals.

    Currently only `disable_stale` becomes an actual `routine.disable`
    proposal — the rest are advisory and surface in the format_summary
    output. We don't auto-propose adjustments to skip-rate / fail-rate
    routines because the right adjustment depends on context the
    slime can't fully reason about (which trigger to widen, which
    judge_prompt to relax, etc.).

    Returns list of approval IDs created.
    """
    from sentinel.growth import submit_action, PolicyDenied

    created: list[str] = []
    for sug in report.suggestions:
        if sug.get("kind") != "disable_stale":
            continue
        rid = sug.get("routine_id")
        if not rid:
            continue
        try:
            approval = submit_action(
                action_type="routine.disable",
                title=sug["title"],
                reason=sug.get("detail", "(reflection suggested)"),
                payload={"id": rid, "reason": "reflection: stale"},
            )
            created.append(approval.id)
        except PolicyDenied as e:
            log.info(
                f"reflection suggestion denied at submit: "
                f"{[f.get('msg') for f in e.findings]}"
            )
        except Exception as e:
            log.warning(f"reflection submit failed: {e}")
    return created
