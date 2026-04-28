"""Human-in-the-loop approval queue.

Two classes of things go through this queue:

  A. Code the slime wrote (SKILL_GEN, SELF_MOD) — a file write on
     approval. Existed since v0.1.
  B. Actions the slime wants to take (ACTION) — a registered handler
     fires on approval. Added in Phase C1 as groundwork for Phase D
     computer-use capabilities.

The mechanism is the same for both: no side effect happens until the
user explicitly approves. `safety.scan_code` for code, a policy_check
callback for actions, provide the "check before asking" layer so the
user isn't bombarded with obviously-unsafe requests.

Flow
----
  1. Caller builds a proposal:
       - Code: submit_for_approval(kind=SKILL_GEN|SELF_MOD, ...)
       - Action: submit_action(action_type=..., payload={...}, ...)
  2. A safety/policy check runs at submit time; findings are stored
     with the proposal (not hidden from the user).
  3. Proposal lands in ~/.hermes/approvals/pending/<id>.json.
     Callbacks registered via register_on_submit fire so GUI +
     Telegram can notify.
  4. User decides:
       - approve(id) — code proposals write to target_path; action
         proposals invoke their registered handler.
       - reject(id, reason) — archived to rejected/ with the reason.
  5. Every transition is audit-logged to approvals.jsonl.

Action handler registry
-----------------------
Modules that want to offer user-approvable actions register a handler
at import time:

    from sentinel.growth import approval

    def _execute_file_open(payload: dict) -> dict:
        # payload validated by policy, execute side effect
        subprocess.run(["start", "", payload["path"]], shell=True)
        return {"ok": True}

    def _policy_file_open(payload: dict) -> tuple[bool, list[dict]]:
        findings = []
        if not Path(payload["path"]).exists():
            findings.append({"level": "error", "msg": "path not found"})
            return False, findings
        return True, findings

    approval.register_action_handler(
        "file_open", handler=_execute_file_open, policy=_policy_file_open,
    )

This file has ZERO side effects on sentinel code or the user's
machine until approve() is called — that's the whole point.

Storage layout under ~/.hermes/approvals/:

  pending/<id>.json   — the proposal (metadata + source or payload)
  approved/<id>.json  — archived after approval (audit trail)
  rejected/<id>.json  — archived after rejection (audit trail)
  approvals.jsonl     — per-transition audit log
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.growth.approval")

APPROVALS_DIR = Path.home() / ".hermes" / "approvals"
PENDING_DIR = APPROVALS_DIR / "pending"
APPROVED_DIR = APPROVALS_DIR / "approved"
REJECTED_DIR = APPROVALS_DIR / "rejected"
AUDIT_LOG = APPROVALS_DIR / "approvals.jsonl"


# ── Proposal types ────────────────────────────────────────────────

SKILL_GEN = "skill_gen"           # new skill file in sentinel/skills/
SELF_MOD = "self_mod"             # modification of a MODIFIABLE_FILES entry
ACTION = "action"                 # generic side-effect action via handler


@dataclass
class PendingApproval:
    """A proposal awaiting user decision.

    Two shapes share this struct:
      - Code proposals (kind ∈ {SKILL_GEN, SELF_MOD}) use
        target_path + source + safety_findings.
      - Action proposals (kind == ACTION) use action_type + payload +
        policy_findings. The registered handler's name is stored in
        action_type and the handler is looked up at approve() time,
        not stored in the proposal itself.

    Both share id/created_at/title/reason so a GUI can render a
    uniform list without branching per kind for display.

    Unused fields default to empty so both old files (pre-C1, no
    action_type/payload/policy_findings) and new files round-trip
    through JSON without migration.
    """
    id: str                       # short random ID, e.g. "a3f7b2"
    kind: str                     # SKILL_GEN | SELF_MOD | ACTION
    created_at: float             # unix timestamp
    title: str                    # short human label
    reason: str                   # why the slime proposed this

    # Code-proposal fields (SKILL_GEN, SELF_MOD). Empty for ACTION.
    target_path: str = ""         # where code will land on approval
    source: str = ""              # the actual code
    previous_source: str = ""     # for SELF_MOD: the code being replaced
    safety_findings: list[dict] = field(default_factory=list)

    # Action-proposal fields (ACTION). Empty for code kinds.
    action_type: str = ""         # e.g. "file_open", "window_focus"
    payload: dict = field(default_factory=dict)
    policy_findings: list[dict] = field(default_factory=list)

    # Common metadata
    proposer_tier: str = ""       # evolution tier at proposal time


# ── Directory setup ───────────────────────────────────────────────

def _ensure_dirs() -> None:
    for d in (PENDING_DIR, APPROVED_DIR, REJECTED_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _new_id() -> str:
    """Short, URL-safe, unique enough for local storage."""
    return secrets.token_hex(3)  # 6 hex chars


# ── Callbacks on submit ───────────────────────────────────────────
# GUI / daemon register here so the user gets notified when a
# proposal is queued. Keeps growth module free of PySide/Telegram
# dependencies.

_submit_callbacks: list = []


def register_on_submit(callback) -> None:
    """Register a callable(PendingApproval) -> None to fire on submit.

    Callbacks MUST NOT raise. If they do, the exception is swallowed
    and logged — one broken listener shouldn't kill the queue.
    """
    if callback not in _submit_callbacks:
        _submit_callbacks.append(callback)


def unregister_on_submit(callback) -> None:
    if callback in _submit_callbacks:
        _submit_callbacks.remove(callback)


# Reject callbacks fire when the user (or any actor) rejects a
# proposal. Phase I feeds these into the routine preferences log so
# the detector learns what NOT to propose. Kept separate from submit
# callbacks because the data + audience differ — submit callbacks are
# UI notifications ("you have a new pending"); reject callbacks are
# learning signals ("don't suggest this kind of thing again").

_reject_callbacks: list = []


def register_on_reject(callback) -> None:
    """Register callback(PendingApproval, reason: str) -> None.

    Same isolation rule as submit callbacks: exceptions are caught +
    logged so one buggy listener can't disrupt the queue.
    """
    if callback not in _reject_callbacks:
        _reject_callbacks.append(callback)


def unregister_on_reject(callback) -> None:
    if callback in _reject_callbacks:
        _reject_callbacks.remove(callback)


# ── Action handler registry ───────────────────────────────────────
# Modules that want to offer user-approvable actions register their
# executor + policy check here. Kept as a simple dict — if there's
# ever a need for dynamic loading or plugin-defined handlers, this is
# the single point to generalize.

_action_handlers: dict[str, dict] = {}


def register_action_handler(
    action_type: str,
    handler,
    policy=None,
) -> None:
    """Register how to execute and pre-check an action type.

    handler: callable(payload: dict) -> dict
        Runs when the user approves. Should carry out the side effect
        and return a structured result (e.g. {"ok": True, "info": ...}).
        Exceptions propagate to approve() which logs and returns False.

    policy: optional callable(payload: dict) -> tuple[bool, list[dict]]
        Runs at submit time. Returns (allowed, findings). If allowed
        is False, submit_action refuses to queue (caller gets the
        findings back and should inform the user). Findings are also
        stored on the proposal so the user sees them when judging.
        Each finding dict: {"level": "warn"|"error"|"info", "msg": str}.

    Re-registering the same action_type replaces the previous handler,
    which makes live reloads during development painless.
    """
    _action_handlers[action_type] = {
        "handler": handler,
        "policy": policy,
    }
    log.debug("action handler registered: %s", action_type)


def list_action_types() -> list[str]:
    """Return known action types. Useful for debugging / tests."""
    return sorted(_action_handlers.keys())


# ── Submit ────────────────────────────────────────────────────────

def _persist_and_notify(approval: PendingApproval) -> PendingApproval:
    """Common tail of submit_for_approval and submit_action.

    Writes the pending file, fires the audit log, runs registered
    on-submit callbacks. Isolated so both code and action paths share
    the same persistence semantics without duplication.
    """
    path = PENDING_DIR / f"{approval.id}.json"
    path.write_text(
        json.dumps(asdict(approval), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _audit("submit", approval.id, {
        "kind": approval.kind,
        "title": approval.title,
        "target": approval.target_path or approval.action_type,
        "warnings": len(approval.safety_findings) + len(approval.policy_findings),
    })
    log.info("Approval queued: %s (%s) — %s",
             approval.id, approval.kind, approval.title)
    for cb in list(_submit_callbacks):
        try:
            cb(approval)
        except Exception as e:
            log.warning("approval submit callback %r raised: %s", cb, e)
    return approval


def submit_for_approval(
    kind: str,
    title: str,
    reason: str,
    target_path: str,
    source: str,
    previous_source: str = "",
    safety_findings: Optional[list[dict]] = None,
    proposer_tier: str = "",
) -> PendingApproval:
    """Queue a code proposal. Does NOT deploy anything.

    Returns the created PendingApproval. Caller is expected to
    notify the user that action is required.
    """
    _ensure_dirs()
    if kind not in (SKILL_GEN, SELF_MOD):
        raise ValueError(f"Unknown code-approval kind: {kind}")

    approval = PendingApproval(
        id=_new_id(),
        kind=kind,
        created_at=time.time(),
        title=title,
        reason=reason,
        target_path=target_path,
        source=source,
        previous_source=previous_source,
        safety_findings=safety_findings or [],
        proposer_tier=proposer_tier,
    )
    return _persist_and_notify(approval)


class PolicyDenied(Exception):
    """Raised by submit_action when policy refuses to even queue.

    Carries the findings list so the caller can display them. Policy
    is the "won't do it" gate; rejection at queue time means the user
    never sees a proposal that was obviously unsafe. Less noise for
    the user, faster feedback for the caller.
    """
    def __init__(self, action_type: str, findings: list[dict]):
        self.action_type = action_type
        self.findings = findings
        msg = "; ".join(f.get("msg", "") for f in findings) or "denied"
        super().__init__(f"policy denied {action_type}: {msg}")


def submit_action(
    action_type: str,
    title: str,
    reason: str,
    payload: Optional[dict] = None,
    proposer_tier: str = "",
) -> PendingApproval:
    """Queue an action for user approval.

    The action_type must have been registered via
    register_action_handler. Its policy check (if any) runs
    immediately; if denied, raises PolicyDenied and nothing is
    queued. Otherwise the proposal lands in pending/ just like a
    code proposal and waits for the user.
    """
    _ensure_dirs()
    entry = _action_handlers.get(action_type)
    if entry is None:
        raise ValueError(
            f"Unknown action_type: {action_type!r}. "
            f"Known: {list_action_types()}"
        )

    payload = payload or {}
    findings: list[dict] = []
    policy_fn = entry.get("policy")
    if policy_fn is not None:
        try:
            allowed, findings = policy_fn(payload)
        except Exception as e:
            log.error("policy check %s raised: %s", action_type, e)
            # Fail closed: if the policy check itself crashes, assume
            # the action isn't safe to queue.
            raise PolicyDenied(action_type, [
                {"level": "error", "msg": f"policy check failed: {e}"}
            ])
        if not allowed:
            raise PolicyDenied(action_type, findings)

    approval = PendingApproval(
        id=_new_id(),
        kind=ACTION,
        created_at=time.time(),
        title=title,
        reason=reason,
        action_type=action_type,
        payload=payload,
        policy_findings=findings,
        proposer_tier=proposer_tier,
    )
    return _persist_and_notify(approval)


# ── Query ─────────────────────────────────────────────────────────

def list_pending() -> list[PendingApproval]:
    """Read all pending proposals, newest first."""
    _ensure_dirs()
    items: list[PendingApproval] = []
    for f in sorted(PENDING_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items.append(PendingApproval(**data))
        except (json.JSONDecodeError, TypeError, OSError) as e:
            log.warning("Corrupted pending file %s: %s", f.name, e)
    return items


def list_history() -> list[dict]:
    """Read all approved + rejected proposals, newest first.

    Each dict has the same shape as PendingApproval (via asdict), plus:
      - "_status": "approved" | "rejected"
      - "_decided_at": float (unix ts) — from audit log or file mtime
      - "_rejection": dict (only for rejected; has reason, at, by)
    """
    _ensure_dirs()
    items: list[dict] = []
    for status_dir, status_label in ((APPROVED_DIR, "approved"),
                                     (REJECTED_DIR, "rejected")):
        for f in status_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["_status"] = status_label
                # Derive decision timestamp
                if status_label == "rejected" and "_rejection" in data:
                    data["_decided_at"] = data["_rejection"].get("at", data.get("created_at", 0))
                else:
                    data["_decided_at"] = f.stat().st_mtime
                items.append(data)
            except (json.JSONDecodeError, TypeError, OSError) as e:
                log.warning("Corrupted %s file %s: %s", status_label, f.name, e)
    # Add pending items too (so the history view shows the full picture)
    for p in list_pending():
        d = asdict(p)
        d["_status"] = "pending"
        d["_decided_at"] = p.created_at
        items.append(d)
    # Sort newest first
    items.sort(key=lambda x: x.get("_decided_at", 0), reverse=True)
    return items


def get_pending(approval_id: str) -> Optional[PendingApproval]:
    path = PENDING_DIR / f"{approval_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PendingApproval(**data)
    except Exception as e:
        log.warning("Read pending %s failed: %s", approval_id, e)
        return None


# ── Decide ────────────────────────────────────────────────────────

def _archive_approved(approval_id: str) -> None:
    """Move pending/<id>.json to approved/<id>.json. Logs on failure
    but never raises — archive issues shouldn't overshadow the actual
    approval outcome.

    Uses os.replace (atomic + overwrites if dst already exists) instead
    of Path.rename; the latter fails on Windows when dst exists, which
    can leave the file in PENDING_DIR after a successful action — the
    card then refuses to disappear from the chat panel.
    """
    src = PENDING_DIR / f"{approval_id}.json"
    dst = APPROVED_DIR / f"{approval_id}.json"
    try:
        import os
        os.replace(src, dst)
    except OSError as e:
        log.warning("approve(%s): archive failed: %s", approval_id, e)


def _execute_code_approval(pending: PendingApproval) -> bool:
    """Write source to target_path for SKILL_GEN / SELF_MOD kinds."""
    target = Path(pending.target_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(pending.source, encoding="utf-8")
        return True
    except OSError as e:
        log.error("approve(%s): write failed: %s", pending.id, e)
        _audit("approve_failed", pending.id, {"error": str(e)})
        return False


def _execute_action_approval(pending: PendingApproval) -> bool:
    """Invoke the registered handler for an ACTION-kind proposal.

    Handler exceptions are caught and logged — a failed action is a
    reject in the eyes of the queue, not a crash of the approval
    system. The handler return value is included in the audit log.
    """
    entry = _action_handlers.get(pending.action_type)
    if entry is None:
        log.error("approve(%s): no handler for action_type=%s",
                  pending.id, pending.action_type)
        _audit("approve_failed", pending.id, {
            "error": f"unknown action_type {pending.action_type}",
        })
        return False
    handler = entry["handler"]
    try:
        result = handler(pending.payload)
        _audit("action_result", pending.id, {
            "action_type": pending.action_type,
            "result": result if isinstance(result, dict) else {"ok": bool(result)},
        })
        return True
    except Exception as e:
        log.error("approve(%s) action %s failed: %s",
                  pending.id, pending.action_type, e)
        _audit("approve_failed", pending.id, {
            "action_type": pending.action_type,
            "error": str(e),
        })
        return False


def approve(approval_id: str, approver: str = "user") -> bool:
    """Carry out the user's "yes" decision.

    Dispatches by kind:
      - SKILL_GEN / SELF_MOD: write source to target_path (unchanged
        behavior from pre-C1).
      - ACTION: invoke the registered handler with payload.

    Archives the pending file to approved/ on success, logs the
    decision to the audit trail. Returns True only if the underlying
    execution succeeded — a False here means the approval was logged
    but the side effect couldn't be carried out (disk error, handler
    exception, unknown action_type).

    Safety note: we do NOT re-run safety / policy checks here. They
    ran at submit time and their findings are visible in the
    proposal. If the user saw the findings and approved anyway,
    that's their call.
    """
    pending = get_pending(approval_id)
    if pending is None:
        log.warning("approve(%s): not found", approval_id)
        return False

    if pending.kind in (SKILL_GEN, SELF_MOD):
        ok = _execute_code_approval(pending)
        log_target = pending.target_path
    elif pending.kind == ACTION:
        ok = _execute_action_approval(pending)
        log_target = pending.action_type
    else:
        log.error("approve(%s): unknown kind %s", approval_id, pending.kind)
        return False

    if not ok:
        return False

    _archive_approved(approval_id)
    _audit("approve", approval_id, {
        "approver": approver,
        "target": log_target,
        "kind": pending.kind,
    })
    log.info("Approved %s (%s) → %s", approval_id, pending.kind, log_target)
    return True


def reject(approval_id: str, reason: str = "",
           approver: str = "user") -> bool:
    """Discard the proposal. Nothing is written to target_path."""
    pending = get_pending(approval_id)
    if pending is None:
        log.warning("reject(%s): not found", approval_id)
        return False

    src = PENDING_DIR / f"{approval_id}.json"
    dst = REJECTED_DIR / f"{approval_id}.json"
    # Annotate in place, then atomically move pending → rejected.
    # The previous "write to dst, unlink src" path could leave a copy
    # in pending if unlink failed under a transient Windows file-lock
    # (indexing service / antivirus), making it look like reject did
    # nothing — the card stayed because list_pending still saw the
    # file. os.replace is atomic on both Windows and POSIX.
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        data["_rejection"] = {
            "reason": reason,
            "at": time.time(),
            "by": approver,
        }
        src.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        import os
        os.replace(src, dst)
    except OSError as e:
        log.error("reject(%s): archive failed: %s", approval_id, e)
        return False

    _audit("reject", approval_id, {
        "approver": approver,
        "reason": reason,
        "kind": pending.kind,
    })
    log.info("Rejected %s (%s)", approval_id, reason or "no reason given")

    # Notify learners (Phase I — routine preferences memory). Each
    # listener isolated: if one crashes others still run, and a crash
    # never reverses the rejection itself.
    for cb in list(_reject_callbacks):
        try:
            cb(pending, reason)
        except Exception as e:
            log.warning("approval reject callback %r raised: %s", cb, e)

    return True


# ── Audit log ─────────────────────────────────────────────────────

def _audit(action: str, approval_id: str, extra: dict) -> None:
    """Append an audit line. Never raises — logging must not break flow."""
    try:
        _ensure_dirs()
        entry = {
            "at": time.time(),
            "action": action,
            "id": approval_id,
            **extra,
        }
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def audit_tail(n: int = 50) -> list[dict]:
    """Read the last n audit entries. For the GUI / Telegram /status."""
    if not AUDIT_LOG.exists():
        return []
    try:
        lines = AUDIT_LOG.read_text(encoding="utf-8").strip().split("\n")
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-n:]:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out
