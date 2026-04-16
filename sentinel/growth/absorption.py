"""Absorption — turn equipped items into permanent body parts.

Peter's observation: 史萊姆就是靠吸收. Slimes absorb things.

Equipment today (sentinel/wallet/equipment.py) is purely reversible:
you equip it, you unequip it, you can sell it on the marketplace.
That's fine for gear, but it doesn't capture the slime's identity.
A slime that's been with you for 6 months through dozens of late-night
sessions should look different from a new one, even if both happen to
have the same sword equipped today.

ABSORPTION FLOW
===============
    ┌─────────────┐   equip    ┌─────────────┐
    │  inventory  │ ─────────> │  equipped   │  (reversible)
    │  (tradeable)│ <───────── │  (tradeable)│
    └─────────────┘  unequip   └─────────────┘
                                      │
                                      │ absorb (one-way)
                                      ▼
                               ┌─────────────┐
                               │  appendage  │  (permanent)
                               │  (locked to │
                               │   this slime)│
                               └─────────────┘

RULES
-----
1. Absorption is IRREVERSIBLE. Once absorbed, the item cannot be
   unequipped, sold, or recovered. User must explicitly confirm in
   a dialog that says this out loud.
2. Absorption requires capability PROPOSE_SKILL or higher (tier
   Demon Lord Seed+). Young slimes cannot absorb — they haven't
   earned the right to mutate themselves. This ties growth to
   trust, not just time.
3. Rarity gate: legendary / mythic / ultimate items require tier
   True Demon Lord+ to absorb. The slime needs to be strong enough
   to hold the item's essence without destabilizing.
4. Absorbed appendages show up in the avatar render (PR 2) — body
   sprite gets overlay or shape mutation based on the slot.
5. Absorbed items are removed from the equipment inventory and
   cannot be listed on the marketplace. The marketplace code needs
   to check this (PR 2 also).

STATUS — HONEST
---------------
This file has the DATA MODEL, persistence, and the can_absorb() check.

What's NOT here yet (PR 2):
  - UI flow in gui.py that offers "吸收" as an option on equipped items
  - Marketplace listing to refuse absorbed item_ids
  - slime_avatar.py / sprite_renderer.py drawing the appendage overlays

So right now: calling absorb() persists state, but the user sees
nothing different in the avatar. That's why this PR doesn't wire it
into the GUI — we'd be half-shipping it.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from sentinel.growth.capability import Capability, can_perform

log = logging.getLogger("sentinel.growth.absorption")

ABSORPTION_FILE = Path.home() / ".hermes" / "slime_absorptions.json"

# Rarity gating: which rarities need which minimum tier to absorb.
# Tiers ranked by position in evolution.EVOLUTION_TIERS.
_RARITY_MIN_TIER = {
    "common":    "Demon Lord Seed",
    "uncommon":  "Demon Lord Seed",
    "rare":      "Demon Lord Seed",
    "epic":      "Demon Lord Seed",
    "legendary": "True Demon Lord",
    "mythic":    "True Demon Lord",
    "ultimate":  "Ultimate Slime",
}

_TIER_ORDER = [
    "Slime", "Slime+", "Named Slime", "Majin",
    "Demon Lord Seed", "True Demon Lord", "Ultimate Slime",
]


@dataclass
class Appendage:
    """A permanent body part grown from an absorbed item."""
    id: str                       # stable ID for diff / render
    slot: str                     # "left_hand" | "helmet" | "eyes" | ...
    source_item_id: str           # equipment item_id that was consumed
    source_template: str          # template_name for display
    source_rarity: str
    source_visual: str            # visual key (for sprite/SVG lookup)
    absorbed_at: float            # unix timestamp
    absorbed_at_tier: str         # evolution tier at absorption time


@dataclass
class AbsorptionState:
    """All appendages for this slime. One file per installation."""
    version: int = 1
    appendages: list[Appendage] = field(default_factory=list)


# ── Persistence ──────────────────────────────────────────────────

def load_state() -> AbsorptionState:
    if not ABSORPTION_FILE.exists():
        return AbsorptionState()
    try:
        raw = json.loads(ABSORPTION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Corrupted absorption file (%s), starting fresh", e)
        return AbsorptionState()
    appendages = [Appendage(**a) for a in raw.get("appendages", [])]
    return AbsorptionState(
        version=raw.get("version", 1),
        appendages=appendages,
    )


def save_state(state: AbsorptionState) -> None:
    ABSORPTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    ABSORPTION_FILE.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Capability + rarity gate ─────────────────────────────────────

@dataclass
class AbsorptionDecision:
    allowed: bool
    reason: str


def can_absorb(rarity: str, tier: Optional[str] = None) -> AbsorptionDecision:
    """Check if the slime is ready to absorb an item of this rarity.

    Two gates:
    1. Capability: slime must be at least Demon Lord Seed (PROPOSE_SKILL)
    2. Rarity: high-rarity items need higher tier
    """
    # Gate 1: capability
    cap_decision = can_perform(Capability.PROPOSE_SKILL, tier=tier)
    if not cap_decision.allowed:
        return AbsorptionDecision(
            allowed=False,
            reason=(
                f"current tier {cap_decision.tier} cannot absorb yet — "
                f"needs Demon Lord Seed or higher"
            ),
        )
    actual_tier = cap_decision.tier

    # Gate 2: rarity
    required = _RARITY_MIN_TIER.get(rarity.lower(), "Demon Lord Seed")
    if _tier_rank(actual_tier) < _tier_rank(required):
        return AbsorptionDecision(
            allowed=False,
            reason=(
                f"{rarity} items need {required}+ to absorb; "
                f"current tier is {actual_tier}"
            ),
        )

    return AbsorptionDecision(
        allowed=True,
        reason=f"{rarity} absorption allowed at {actual_tier}",
    )


def _tier_rank(tier: str) -> int:
    try:
        return _TIER_ORDER.index(tier)
    except ValueError:
        return -1


# ── Absorption (the one-way door) ────────────────────────────────

def absorb_equipment(
    item_id: str,
    template_name: str,
    rarity: str,
    slot: str,
    visual: str,
    tier: Optional[str] = None,
) -> Optional[Appendage]:
    """Convert an equipped item into a permanent appendage.

    Returns the new Appendage on success, None on refusal. The caller
    is responsible for:
      - removing item_id from equipment inventory (sentinel/wallet/
        equipment.py)
      - re-rendering the avatar

    This function does NOT touch equipment inventory itself, because
    doing so would couple growth to wallet. The contract: you pass us
    the facts, we record the absorption, you clean up.
    """
    decision = can_absorb(rarity, tier=tier)
    if not decision.allowed:
        log.info("Absorption refused: %s", decision.reason)
        return None

    # Current tier (may have been computed above, but re-read to be sure)
    try:
        from sentinel.evolution import load_evolution
        actual_tier = tier or load_evolution().form
    except Exception:
        actual_tier = tier or "Slime"

    appendage = Appendage(
        id=f"ap_{int(time.time())}_{item_id[:8]}",
        slot=slot,
        source_item_id=item_id,
        source_template=template_name,
        source_rarity=rarity,
        source_visual=visual,
        absorbed_at=time.time(),
        absorbed_at_tier=actual_tier,
    )
    state = load_state()
    state.appendages.append(appendage)
    save_state(state)
    log.info(
        "Absorbed %s (%s) into %s slot",
        template_name, rarity, slot,
    )
    return appendage


def is_absorbed(item_id: str) -> bool:
    """Check if an item_id has already been absorbed.

    Marketplace listing code should call this to refuse trades on
    absorbed items. PR 2 will wire this into the listing flow.
    """
    state = load_state()
    return any(a.source_item_id == item_id for a in state.appendages)


def list_appendages() -> list[Appendage]:
    """Return all absorbed appendages, oldest first (growth order)."""
    return sorted(load_state().appendages, key=lambda a: a.absorbed_at)
