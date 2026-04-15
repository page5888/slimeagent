"""Auth endpoints."""
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from jose import jwt
from pydantic import BaseModel
from server import config
from server.db.engine import get_db
from server.auth.google import verify_google_token
from server.auth.deps import get_current_user
from server.wallet.service import get_wallet_client

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    google_token: str
    referral_code: str = ""


class LoginResponse(BaseModel):
    token: str
    uid: str
    email: str
    display_name: str
    referral_code: str
    balance: int


def _make_jwt(user_id: str, google_sub: str, email: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRE_HOURS)
    return jwt.encode(
        {"user_id": user_id, "google_sub": google_sub, "email": email, "exp": exp},
        config.JWT_SECRET, algorithm=config.JWT_ALGORITHM,
    )


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    # Verify Google token
    try:
        google_info = await verify_google_token(req.google_token)
    except Exception:
        raise HTTPException(401, "Invalid Google token")

    db = await get_db()
    google_sub = google_info["sub"]

    # Upsert user
    row = await db.execute_fetchone(
        "SELECT id, wallet_uid, referral_code FROM users WHERE google_sub = ?",
        (google_sub,),
    )

    if row:
        user_id = row["id"]
        await db.execute(
            "UPDATE users SET last_login_at = datetime('now'), "
            "display_name = ?, photo_url = ? WHERE id = ?",
            (google_info["name"], google_info["picture"], user_id),
        )
        await db.commit()
        wallet_uid = row["wallet_uid"]
        referral_code = row["referral_code"]
    else:
        user_id = str(uuid.uuid4())
        wallet_uid = ""
        referral_code = ""

        # Register with 5888 wallet
        wc = get_wallet_client()
        if wc:
            try:
                wr = wc.ensure_user(
                    google_sub, google_info["email"],
                    google_info["name"], google_info["picture"],
                    req.referral_code,
                )
                wallet_uid = wr.get("uid", "")
                referral_code = wr.get("referralCode", "")
            except Exception:
                pass  # wallet registration failed, continue without it

        await db.execute(
            "INSERT INTO users (id, google_sub, email, display_name, photo_url, "
            "wallet_uid, referral_code) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, google_sub, google_info["email"], google_info["name"],
             google_info["picture"], wallet_uid, referral_code),
        )
        await db.commit()

    # Get balance
    balance = 0
    wc = get_wallet_client()
    if wc and wallet_uid:
        try:
            br = wc.get_balance(wallet_uid)
            balance = br.get("balance", 0)
        except Exception:
            pass

    token = _make_jwt(user_id, google_sub, google_info["email"])

    return LoginResponse(
        token=token,
        uid=user_id,
        email=google_info["email"],
        display_name=google_info["name"],
        referral_code=referral_code,
        balance=balance,
    )


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT id, email, display_name, photo_url, referral_code, created_at "
        "FROM users WHERE id = ?",
        (user["user_id"],),
    )
    if not row:
        raise HTTPException(404, "User not found")
    return dict(row)
