"""5888 wallet costs that survived the marketplace archive.

`wallet/market_rules.py` was archived per ADR
2026-04-30-slime-stays-private.md (it was the marketplace pricing
config). But two constants in there were also load-bearing for
non-marketplace flows: evolution cost paid in 5888 points.

Moved here so:
  - server/evolution/router.py keeps a stable import path
  - sentinel/gui.py EvolutionTab keeps a stable import path
  - the marketplace-related rest of market_rules.py can be archived
    cleanly without dragging EVOLVE_COST with it

If a future version revisits 5888 economy design (free tier vs paid,
sunset protocol, etc), this file is the central place to look.
Originally lived at sentinel/wallet/market_rules.py:76, :108.
"""

# Evolution costs the master 2 5888 points each time.
# Identifier used by the wallet's spend-type accounting.
SPEND_TYPE_EVOLVE = "slime_evolve"
EVOLVE_COST = 2
