"""Growth system for AI Slime Agent.

This package is the safety-first foundation for the slime's ability to
grow over time — learn new skills, unlock new capabilities, and
eventually modify its own code. It builds on (not replaces) the
existing evolution.py, slime_avatar.py, and self_evolution.py.

STATUS — HONEST MAP
====================
This is PR 1 of 5. Read sentinel/growth/README.md for the full plan.

What this package delivers and actually works:
  - safety.py         AST-based code scanner. Replaces string-match
                      safety in self_evolution.py.
  - approval.py       Pending queue. Generated skills / self-mods go
                      here first; a human must approve before they
                      become runnable.
  - capability.py     Permission tiers derived from evolution form.
                      Hard gate on what the slime is ALLOWED to do.
  - absorption.py     Data model for absorbed equipment (permanent
                      body mutation). Stored but not yet rendered.
  - federation.py     Cross-user learning protocol DESIGN ONLY.
                      No network calls yet — see PR 5.

What is intentionally NOT in this PR:
  - Parametric SVG renderer — PR 2 (GUI integration)
  - LLM-driven memory crystallization — PR 3
  - Sandboxed skill execution runtime — PR 4
  - Federation server endpoints — PR 5

Why scoped this way: the most critical missing piece is the approval
gate. Right now self_evolution.py auto-deploys LLM-written code with
no human confirmation. This PR closes that hole first.
"""
from sentinel.growth.capability import (
    Capability,
    current_capabilities,
    can_perform,
)
from sentinel.growth.approval import (
    PendingApproval,
    submit_for_approval,
    list_pending,
    approve,
    reject,
    register_on_submit,
    unregister_on_submit,
)
from sentinel.growth.safety import (
    scan_code,
    SafetyReport,
)

__all__ = [
    "Capability", "current_capabilities", "can_perform",
    "PendingApproval", "submit_for_approval", "list_pending",
    "approve", "reject",
    "register_on_submit", "unregister_on_submit",
    "scan_code", "SafetyReport",
]
