"""Human-in-the-loop approval queue for slime-authored code.

The slime never deploys code it wrote. Instead:

  1. self_evolution.generate_skill() or self_modify() produces code.
  2. safety.scan_code() catches obvious bad patterns.
  3. submit_for_approval() writes the proposal to a pending file and
     notifies the user (Telegram if configured, GUI list otherwise).
  4. The USER decides: approve() moves the code into the runnable
     location; reject() deletes it and logs why.

Storage layout under ~/.hermes/approvals/:

  pending/<id>.json   — the proposal (metadata + source)
  approved/<id>.json  — archived after approval (audit trail)
  rejected/<id>.json  — archived after rejection (audit trail)

Every approve / reject action is logged to approvals.jsonl with
timestamp, user decision, and findings from the safety scan. This
gives a full audit of what the slime proposed and what the user
accepted over time.

This file has ZERO side effects on sentinel code until approve() is
called — that's the whole point.
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


@dataclass
class PendingApproval:
    """A proposal awaiting user decision.

    Fields chosen to stay readable by a human opening the JSON:
    everything needed to judge the proposal is in one file.
    """
    id: str                       # short random ID, e.g. "a3f7b2"
    kind: str                     # SKILL_GEN or SELF_MOD
    created_at: float             # unix timestamp
    title: str                    # short human label
    reason: str                   # why the slime proposed this
    target_path: str              # where code will land on approval
    source: str                   # the actual code
    safety_findings: list[dict] = field(default_factory=list)
    previous_source: str = ""     # for SELF_MOD: the code being replaced
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


# ── Submit ────────────────────────────────────────────────────────

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
    """Queue a proposal. Does NOT deploy anything.

    Returns the created PendingApproval. Caller is expected to
    notify the user that action is required.
    """
    _ensure_dirs()
    if kind not in (SKILL_GEN, SELF_MOD):
        raise ValueError(f"Unknown approval kind: {kind}")

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
    path = PENDING_DIR / f"{approval.id}.json"
    path.write_text(
        json.dumps(asdict(approval), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _audit("submit", approval.id, {
        "kind": kind,
        "title": title,
        "target": target_path,
        "warnings": len(approval.safety_findings),
    })
    log.info("Approval queued: %s (%s) — %s", approval.id, kind, title)

    # Fire callbacks — each listener isolated so one crash doesn't stop the rest
    for cb in list(_submit_callbacks):
        try:
            cb(approval)
        except Exception as e:
            log.warning("approval submit callback %r raised: %s", cb, e)

    return approval


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

def approve(approval_id: str, approver: str = "user") -> bool:
    """Deploy the proposal by writing its source to target_path.

    Archives the pending file to approved/ after a successful write.
    Returns True on success.

    Safety note: we do NOT re-run the safety scanner here. The scanner
    already ran at submit time and its findings are in the proposal.
    The human saw those findings before approving. If they approved
    anyway, that's their call — we log it and proceed.
    """
    pending = get_pending(approval_id)
    if pending is None:
        log.warning("approve(%s): not found", approval_id)
        return False

    target = Path(pending.target_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(pending.source, encoding="utf-8")
    except OSError as e:
        log.error("approve(%s): write failed: %s", approval_id, e)
        _audit("approve_failed", approval_id, {"error": str(e)})
        return False

    # Move pending file to approved archive
    src = PENDING_DIR / f"{approval_id}.json"
    dst = APPROVED_DIR / f"{approval_id}.json"
    try:
        src.rename(dst)
    except OSError as e:
        # Write succeeded but archive failed — keep going but log
        log.warning("approve(%s): archive failed: %s", approval_id, e)

    _audit("approve", approval_id, {
        "approver": approver,
        "target": pending.target_path,
        "kind": pending.kind,
    })
    log.info("Approved %s → %s", approval_id, pending.target_path)
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
    # Annotate the archived file with rejection reason
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        data["_rejection"] = {
            "reason": reason,
            "at": time.time(),
            "by": approver,
        }
        dst.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        src.unlink()
    except OSError as e:
        log.error("reject(%s): archive failed: %s", approval_id, e)
        return False

    _audit("reject", approval_id, {
        "approver": approver,
        "reason": reason,
        "kind": pending.kind,
    })
    log.info("Rejected %s (%s)", approval_id, reason or "no reason given")
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
