"""Single source of truth for marketplace economic rules.

Both server/marketplace/router.py (cross-user trading) and
sentinel/wallet/equipment.py (desktop local state) import from here
so the two can never drift. These match the contract with 5888 side
documented in project_marketplace.md.

Changes here must be coordinated with the 5888 wallet team — the
listing fee deduction types (`slime_list_fee`, `slime_evolve`) are
whitelisted on their s2sSpend endpoint.
"""
from __future__ import annotations

# ── Price bounds (locked with 5888) ─────────────────────────────

MIN_LIST_PRICE = 50       # below this → listing rejected
MAX_LIST_PRICE = 10_000   # above this → listing rejected

# Per-account daily listing cap (AI Slime enforces, not 5888)
DAILY_LIST_CAP = 5

# Listing expiry: unsold items auto-delist after this many days
LISTING_EXPIRY_DAYS = 7

# Sale split on every successful sale. 5888 applies this atomically.
# Documented here for UI display only — do NOT re-implement the split.
SELLER_SHARE = 0.70
L1_COMMISSION = 0.15
L2_COMMISSION = 0.05
SINK_SHARE = 0.10


# ── Tiered listing fee ──────────────────────────────────────────
# Bands chosen with 5888 side: low tier subsidized (so 50pt items
# are viable), high tier surcharged (to discourage trash-listing
# expensive items for visibility). See project_marketplace.md.
#
# Bands are (min_price_inclusive, fee). Ordered ascending. Last
# entry acts as fallback for anything above its min.
LISTING_FEE_BANDS: list[tuple[int, int]] = [
    (50, 2),       # 50 – 99
    (100, 10),     # 100 – 999
    (1_000, 20),   # 1,000 – 4,999
    (5_000, 30),   # 5,000+
]


def listing_fee(price: int) -> int:
    """Return the 5888-point listing fee owed for a given sale price.

    Raises ValueError if price is outside the allowed range — callers
    should validate up front, but this is the defense-in-depth check.
    """
    if price < MIN_LIST_PRICE:
        raise ValueError(
            f"price {price} below minimum {MIN_LIST_PRICE}"
        )
    if price > MAX_LIST_PRICE:
        raise ValueError(
            f"price {price} above maximum {MAX_LIST_PRICE}"
        )
    fee = LISTING_FEE_BANDS[0][1]  # default to lowest band
    for min_price, band_fee in LISTING_FEE_BANDS:
        if price >= min_price:
            fee = band_fee
        else:
            break
    return fee


# ── 5888 reason strings (sitePolicy.ts whitelist on 5888 side) ──
# Any reason not in the whitelist → 5888 returns 403 SITE_NOT_AUTHORIZED.
# To add more, PR sitePolicy.ts on 5888 side first.
#
# spend reasons:
SPEND_TYPE_EVOLVE = "slime_evolve"          # 2 pts, per evolution trigger
SPEND_TYPE_LIST_FEE = "slime_list_fee"      # tiered, per listing
SPEND_TYPE_BUY_SETTLE = "slime_buy_settle"  # buyer deduction at purchase
SPEND_TYPE_CREATOR_REWARD = "slime_creator_reward"  # voter → creator tip (10 pts/vote)
# NOTE on slime_creator_reward — Phase 1 flow (current):
#   Voter pays via s2sSpend(reason=slime_creator_reward). Points go to
#   5888 platform pool; creator credit is tracked LOCALLY in our
#   `creator_reward_ledger` table, NOT transferred through 5888.
# Phase 2 (when 5888 ships s2sCreatorRewardSettle, target Week 5-6):
#   A migration walks ledger rows with status='pending' and calls the
#   atomic settle endpoint. We stop writing new ledger rows and call
#   settle inline from cast_vote instead.

# grant reasons (applied atomically by 5888's marketSaleSettle endpoint
# once it ships — we do NOT call grant with these ourselves):
GRANT_TYPE_SALE_PROCEEDS = "slime_sale_proceeds"      # 70% → seller
GRANT_TYPE_L1_COMMISSION = "slime_l1_commission"      # 15% → L1 upline (fallback: platform pool)
GRANT_TYPE_L2_COMMISSION = "slime_l2_commission"      # 5% → L2 upline (fallback: platform pool)
GRANT_TYPE_PLATFORM_POOL = "slime_platform_pool"      # 10% → platform

# refund reasons:
REFUND_TYPE_ORDER = "slime_order_refund"
REFUND_TYPE_DISPUTE = "slime_dispute_refund"

EVOLVE_COST = 2
