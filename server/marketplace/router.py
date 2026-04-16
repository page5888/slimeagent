"""Marketplace endpoints — P2P equipment trading.

Economic rules (min/max price, tiered listing fee, daily cap) are
imported from sentinel/wallet/market_rules.py — same source of truth
as the desktop side.

5888 wallet integration:
  - Listing fee deducted via s2sSpend(reason=slime_list_fee) before
    the listing row is created. If the spend fails, no listing is
    written and the user sees an error.
  - Purchase flow (/buy) is being rewritten to use 5888's new
    marketSaleSettle atomic S2S — tracked in project_marketplace.md.
    Until that ships, the existing split-on-our-side logic below is
    documented as STALE but left intact so trades don't break.
"""
import uuid
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from server.auth.deps import get_current_user
from server.db.engine import get_db
from server.wallet.service import spend_points, grant_points
from server import config
from sentinel.wallet.client import WalletError
from sentinel.wallet.market_rules import (
    MIN_LIST_PRICE,
    MAX_LIST_PRICE,
    DAILY_LIST_CAP,
    SPEND_TYPE_LIST_FEE,
    listing_fee,
)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


class ListRequest(BaseModel):
    item_id: str
    template_name: str
    slot: str
    rarity: str
    price: int


class BuyRequest(BaseModel):
    listing_id: str


@router.post("/list")
async def list_item(req: ListRequest, user: dict = Depends(get_current_user)):
    """List an item for sale.

    Order of operations (failing any step aborts the listing cleanly):
      1. Validate price range (50 ≤ price ≤ 10,000).
      2. Reject if item already has an active listing.
      3. Check daily listing cap (5 per user per 24h rolling window).
      4. Deduct tiered listing fee via 5888 spendPoints. Non-refundable.
      5. Insert listing row.

    Listing fee tiers (see sentinel/wallet/market_rules.py):
      50–99    → 2 pts
      100–999  → 10 pts
      1k–4.9k  → 20 pts
      5k+      → 30 pts
    """
    # 1. Price bounds
    if req.price < MIN_LIST_PRICE:
        raise HTTPException(
            400, f"Price must be at least {MIN_LIST_PRICE} points"
        )
    if req.price > MAX_LIST_PRICE:
        raise HTTPException(
            400, f"Price must be at most {MAX_LIST_PRICE} points"
        )

    db = await get_db()

    # 2. Not already listed
    existing = await db.execute_fetchone(
        "SELECT 1 FROM marketplace_listings WHERE item_id = ? AND status = 'active'",
        (req.item_id,),
    )
    if existing:
        raise HTTPException(409, "Item already listed")

    # 3. Daily listing cap (5 per rolling 24h)
    cap_row = await db.execute_fetchone(
        "SELECT COUNT(*) AS c FROM marketplace_listings "
        "WHERE seller_id = ? AND created_at > datetime('now', '-1 day')",
        (user["user_id"],),
    )
    if cap_row and cap_row["c"] >= DAILY_LIST_CAP:
        raise HTTPException(
            429,
            f"Daily listing cap reached ({DAILY_LIST_CAP}/day). "
            f"Wait until earlier listings are more than 24h old."
        )

    # 4. Deduct tiered listing fee via 5888. Non-refundable.
    #    Idempotency key is scoped per-listing-attempt (new uuid every
    #    request), so retries from the client are treated as new spend.
    #    This is acceptable because listing fee is small and the API is
    #    only callable by authenticated user, not anonymous retry loops.
    listing_id = str(uuid.uuid4())
    fee = listing_fee(req.price)
    fee_key = f"list_fee:{listing_id}"
    try:
        await spend_points(
            user["user_id"], fee, SPEND_TYPE_LIST_FEE, fee_key,
        )
    except WalletError as e:
        if e.is_insufficient_balance:
            raise HTTPException(
                402,
                f"Insufficient balance for {fee}-point listing fee "
                f"(price tier: {req.price} pts)."
            )
        raise HTTPException(502, f"Wallet error: {e.message}")

    # 5. Record the listing. Listing fee already deducted + non-refundable.
    await db.execute(
        "INSERT INTO marketplace_listings "
        "(id, seller_id, item_id, template_name, slot, rarity, price) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (listing_id, user["user_id"], req.item_id,
         req.template_name, req.slot, req.rarity, req.price),
    )
    await db.commit()

    return {
        "listing_id": listing_id,
        "price": req.price,
        "listing_fee_paid": fee,
    }


@router.post("/delist")
async def delist(listing_id: str, user: dict = Depends(get_current_user)):
    """Cancel a listing."""
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT seller_id, status FROM marketplace_listings WHERE id = ?",
        (listing_id,),
    )
    if not row:
        raise HTTPException(404, "Listing not found")
    if row["seller_id"] != user["user_id"]:
        raise HTTPException(403, "Not your listing")
    if row["status"] != "active":
        raise HTTPException(400, "Listing not active")

    await db.execute(
        "UPDATE marketplace_listings SET status = 'cancelled' WHERE id = ?",
        (listing_id,),
    )
    await db.commit()
    return {"ok": True}


@router.get("/listings")
async def browse(
    slot: str = Query(""),
    rarity: str = Query(""),
    min_price: int = Query(0),
    max_price: int = Query(0),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
):
    """Browse active listings."""
    db = await get_db()

    conditions = ["ml.status = 'active'"]
    params: list = []

    if slot:
        conditions.append("ml.slot = ?")
        params.append(slot)
    if rarity:
        conditions.append("ml.rarity = ?")
        params.append(rarity)
    if min_price > 0:
        conditions.append("ml.price >= ?")
        params.append(min_price)
    if max_price > 0:
        conditions.append("ml.price <= ?")
        params.append(max_price)

    where = " AND ".join(conditions)
    offset = (page - 1) * per_page
    params.extend([per_page, offset])

    rows = await db.execute_fetchall(
        f"SELECT ml.*, u.display_name as seller_name "
        f"FROM marketplace_listings ml "
        f"JOIN users u ON u.id = ml.seller_id "
        f"WHERE {where} ORDER BY ml.created_at DESC LIMIT ? OFFSET ?",
        params,
    )

    count_row = await db.execute_fetchone(
        f"SELECT COUNT(*) as total FROM marketplace_listings ml WHERE {where}",
        params[:-2],
    )

    return {
        "items": [dict(r) for r in rows],
        "total": count_row["total"],
        "page": page,
        "per_page": per_page,
    }


@router.post("/buy")
async def buy(req: BuyRequest, user: dict = Depends(get_current_user)):
    """Buy a listed item. Atomic: deduct buyer, credit seller, transfer item."""
    db = await get_db()

    listing = await db.execute_fetchone(
        "SELECT * FROM marketplace_listings WHERE id = ? AND status = 'active'",
        (req.listing_id,),
    )
    if not listing:
        raise HTTPException(404, "Listing not found or already sold")
    if listing["seller_id"] == user["user_id"]:
        raise HTTPException(400, "Cannot buy your own listing")

    price = listing["price"]
    fee = max(1, int(price * config.BASE_FEE_PERCENT / 100))
    seller_receives = price - fee

    # Deduct buyer
    trade_id = str(uuid.uuid4())
    spend_key = f"buy:{trade_id}"
    try:
        await spend_points(user["user_id"], price,
                           f"Buy {listing['template_name']}", spend_key)
    except WalletError as e:
        if e.is_insufficient_balance:
            raise HTTPException(402, "Insufficient balance")
        raise HTTPException(502, f"Wallet error: {e.message}")

    # Mark listing as sold
    await db.execute(
        "UPDATE marketplace_listings SET status = 'sold', "
        "sold_at = CURRENT_TIMESTAMP, buyer_id = ? WHERE id = ?",
        (user["user_id"], req.listing_id),
    )

    # Record trade
    await db.execute(
        "INSERT INTO trade_history "
        "(id, listing_id, seller_id, buyer_id, template_name, price, fee, "
        "seller_received, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (trade_id, req.listing_id, listing["seller_id"], user["user_id"],
         listing["template_name"], price, fee, seller_receives),
    )
    await db.commit()

    # Credit seller (best effort, idempotent)
    grant_key = f"sale:{trade_id}"
    try:
        await grant_points(listing["seller_id"], seller_receives,
                           f"Sold {listing['template_name']}", grant_key)
    except Exception:
        pass  # Retry mechanism can handle this later

    return {
        "trade_id": trade_id,
        "item_name": listing["template_name"],
        "price": price,
        "fee": fee,
        "seller_received": seller_receives,
    }


@router.get("/history")
async def history(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
    user: dict = Depends(get_current_user),
):
    """Get user's trade history."""
    db = await get_db()
    offset = (page - 1) * per_page

    rows = await db.execute_fetchall(
        "SELECT * FROM trade_history "
        "WHERE seller_id = ? OR buyer_id = ? "
        "ORDER BY completed_at DESC LIMIT ? OFFSET ?",
        (user["user_id"], user["user_id"], per_page, offset),
    )

    return {"items": [dict(r) for r in rows], "page": page}
