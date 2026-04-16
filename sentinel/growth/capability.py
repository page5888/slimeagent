"""Capability tiers — what the slime is ALLOWED to do at its current form.

Maps evolution tier (from evolution.py) → permission set. This is a
HARD GATE: even if self_evolution.py wants to generate a skill, if the
current tier doesn't have SELF_AUTHOR permission, the request is
refused before it ever reaches an LLM.

Design intent:
- Early slimes can only observe. They cannot talk, cannot take action,
  cannot write code. This isn't arbitrary — it's so the slime earns
  trust by being useful in low-stakes ways first.
- Mid-tier slimes can respond and take bounded actions (search files,
  open tools).
- High-tier slimes (Majin+) may propose new skills, but those still
  require human approval via approval.py.
- No tier gets "modify core sentinel code without approval". That
  permission does not exist in this system.

Important: capability is a CEILING, not a requirement. A high-tier
slime can still operate silently if the user prefers. Unlocks allow;
they don't force.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Capability(str, Enum):
    """Discrete permissions. Checked individually — not a bitfield."""

    OBSERVE = "observe"                 # watch system, log events
    NOTIFY = "notify"                   # send Telegram/GUI messages
    RESPOND = "respond"                 # reply when user asks something
    ANALYZE = "analyze"                 # run LLM analysis of observations
    ACT_READ = "act_read"               # read files outside WATCH_DIRS,
                                        # search the web, open docs
    ACT_WRITE = "act_write"             # write files the user owns
                                        # (scratchpads only, not system files)
    PROPOSE_SKILL = "propose_skill"     # submit a new skill to approval
                                        # queue (does NOT deploy it)
    PROPOSE_SELF_MOD = "propose_self_mod"  # submit a self-modification
                                           # to approval queue
    # NOTE: there is no DEPLOY_SKILL capability. Deployment only happens
    # via approval.approve() which requires human confirmation. This is
    # intentional — the slime can never skip the human.


# Evolution tier → capability set.
# Tier names match evolution.EVOLUTION_TIERS. Keep in sync.
_TIER_CAPABILITIES: dict[str, set[Capability]] = {
    "Slime": {
        Capability.OBSERVE,
    },
    "Slime+": {
        Capability.OBSERVE,
        Capability.NOTIFY,
        Capability.RESPOND,
    },
    "Named Slime": {
        Capability.OBSERVE,
        Capability.NOTIFY,
        Capability.RESPOND,
        Capability.ANALYZE,
    },
    "Majin": {
        Capability.OBSERVE,
        Capability.NOTIFY,
        Capability.RESPOND,
        Capability.ANALYZE,
        Capability.ACT_READ,
    },
    "Demon Lord Seed": {
        Capability.OBSERVE,
        Capability.NOTIFY,
        Capability.RESPOND,
        Capability.ANALYZE,
        Capability.ACT_READ,
        Capability.ACT_WRITE,
        Capability.PROPOSE_SKILL,
    },
    "True Demon Lord": {
        Capability.OBSERVE,
        Capability.NOTIFY,
        Capability.RESPOND,
        Capability.ANALYZE,
        Capability.ACT_READ,
        Capability.ACT_WRITE,
        Capability.PROPOSE_SKILL,
        Capability.PROPOSE_SELF_MOD,
    },
    "Ultimate Slime": {
        # Same as True Demon Lord. Evolution beyond this point
        # doesn't grant more power — the ceiling is PROPOSE.
        # Deployment always routes through human approval.
        Capability.OBSERVE,
        Capability.NOTIFY,
        Capability.RESPOND,
        Capability.ANALYZE,
        Capability.ACT_READ,
        Capability.ACT_WRITE,
        Capability.PROPOSE_SKILL,
        Capability.PROPOSE_SELF_MOD,
    },
}


@dataclass(frozen=True)
class CapabilityDecision:
    """Result of a permission check. Explicit so callers can log why."""
    allowed: bool
    tier: str
    capability: Capability
    reason: str


def current_capabilities(tier: Optional[str] = None) -> set[Capability]:
    """Return the set of capabilities available at this tier.

    If tier is None, reads live evolution state. Callers that already
    have the EvolutionState can pass state.form directly to avoid a
    disk read.
    """
    if tier is None:
        try:
            from sentinel.evolution import load_evolution
            tier = load_evolution().form
        except Exception:
            # If evolution state is unreadable, default to most
            # restricted tier. Safer than guessing.
            tier = "Slime"
    return set(_TIER_CAPABILITIES.get(tier, {Capability.OBSERVE}))


def can_perform(cap: Capability, tier: Optional[str] = None) -> CapabilityDecision:
    """Check if a capability is allowed at this tier.

    Use this as a guard at the START of any action that needs a
    capability. Example:

        decision = can_perform(Capability.PROPOSE_SKILL)
        if not decision.allowed:
            log.info("Skipped skill generation: %s", decision.reason)
            return

    The decision is ALWAYS logged — even allows — so we have an audit
    trail of what the slime has been permitted to do.
    """
    caps = current_capabilities(tier)
    actual_tier = tier or "Slime"
    if cap in caps:
        return CapabilityDecision(
            allowed=True,
            tier=actual_tier,
            capability=cap,
            reason=f"{cap.value} granted at tier {actual_tier}",
        )
    return CapabilityDecision(
        allowed=False,
        tier=actual_tier,
        capability=cap,
        reason=f"{cap.value} not yet unlocked (current tier: {actual_tier})",
    )


if __name__ == "__main__":
    # Quick sanity dump — useful for debugging tier definitions.
    for tier_name, caps in _TIER_CAPABILITIES.items():
        print(f"\n{tier_name}:")
        for c in sorted(caps, key=lambda x: x.value):
            print(f"  + {c.value}")
