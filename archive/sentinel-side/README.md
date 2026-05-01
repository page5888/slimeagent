# archive/sentinel-side/

Client-side modules archived per the same ADR as `archive/server-side/`:
[`docs/decisions/2026-04-30-slime-stays-private.md`](../../docs/decisions/2026-04-30-slime-stays-private.md).

## Contents

### `growth/federation.py`

The 公頻 client. Submitted distill-output candidates to the federation server, polled the community pool, surfaced new patterns to the user. Removed because Slime-to-Slime knowledge pooling collides with co-sediment ADR's *「兩個用同一份程式的人，3 年後養出兩隻完全不同的 Slime」*.

### `wallet/equipment.py`

12-slot × 7-rarity equipment system: drops, inventory, equip/unequip, synthesize, list-for-sale, etc. Removed because equipment is a showing-off mechanic — slime is meant to be private, not a comparison object.

### `wallet/market_rules.py`

Marketplace pricing: per-rarity prices, creator reward percentages, transaction fee schedule, evolution cost. Most of it is dead with the marketplace gone. The two constants that survived (`EVOLVE_COST` + `SPEND_TYPE_EVOLVE`) moved to [`sentinel/wallet/costs.py`](../../sentinel/wallet/costs.py) so `server/evolution/router.py` and `EvolutionTab` keep functioning.

### `equipment_visuals.py`

The visual rendering layer for equipped items: `EquipmentIcon`, `VISUAL_REGISTRY`, `get_skin_override`, `render_equipment`. With equipment archived, slime renders as default form (overlay/avatar/sprite_renderer all stub `_load_equipped_visuals` to return empty dict). Visual differentiation comes back in v0.8 via `birth_signature` per [ADR `2026-05-01-slime-physical-individuation.md`](../../docs/decisions/2026-05-01-slime-physical-individuation.md) — different mechanism (innate physical traits, not collected loot).

## What this archive does NOT contain

- `sentinel/wallet/auth.py` / `client.py` / `quota.py` / `costs.py` — the wallet itself stays. 5888 economy is broader than the marketplace.
- `sentinel/identity.py` / `overlay.py` / `slime_avatar.py` / `sprite_renderer.py` — these were modified in-place to drop equipment dependencies, not archived.
- Server-side equivalents — see `archive/server-side/README.md` (federation/equipment/marketplace routers).

## When to reach for this archive

If a future ADR revisits how slime accumulates visual variation (the `birth_signature` v0.8 work, or whatever replaces it), the rendering primitives in `equipment_visuals.py` may be useful as a reference. Don't restore the equipment system itself — that's the part the ADR explicitly decided against.
