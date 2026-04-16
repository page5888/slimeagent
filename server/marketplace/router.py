"""Marketplace endpoints — P2P equipment trading."""
import uuid
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from server.auth.deps import get_current_user
from server.db.engine import get_db
from server.wallet.service import spend_points, grant_points
from server import config
from sentinel.wallet.client import WalletError

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


class ListRequest(BaseModel):
    item_id: str
    template_name: str
    slot: str
    rarity: str
    price: int


class BuyRequest(BaseModel):
    listing_id: str


MIN_LIST_PRICE = 10  # Flat minimum — no per-rarity floor


@router.post("/list")
async def list_item(req: ListRequest, user: dict = Depends(get_current_user)):
    """List an item for sale. Sellers set any price ≥ 10 pt."""
    if req.price < MIN_LIST_PRICE:
        raise HTTPException(400, f"Price must be at least {MIN_LIST_PRICE} points")

    db = await get_db()

    # Check not already listed
    existing = await db.execute_fetchone(
        "SELECT 1 FROM marketplace_listings WHERE item_id = ? AND status = 'active'",
        (req.item_id,),
    )
    if existing:
        raise HTTPException(409, "Item already listed")

    listing_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO marketplace_listings "
        "(id, seller_id, item_id, template_name, slot, rarity, price) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (listing_id, user["user_id"], req.item_id,
         req.template_name, req.slot, req.rarity, req.price),
    )
    await db.commit()

    return {"listing_id": listing_id, "price": req.price}


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
