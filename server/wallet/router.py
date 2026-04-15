"""Wallet endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from server.auth.deps import get_current_user
from server.wallet.service import get_wallet_client, get_wallet_uid

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.post("/balance")
async def balance(user: dict = Depends(get_current_user)):
    wc = get_wallet_client()
    if not wc:
        raise HTTPException(503, "Wallet not configured")
    uid = await get_wallet_uid(user["user_id"])
    if not uid:
        raise HTTPException(400, "No wallet linked")
    try:
        result = wc.get_balance(uid)
        return {"balance": result.get("balance", 0)}
    except Exception as e:
        raise HTTPException(502, f"Wallet error: {e}")
