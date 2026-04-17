"""FastAPI dependencies for authentication."""
from fastapi import Depends, HTTPException, Header
from jose import jwt, JWTError
from server import config


async def get_current_user(authorization: str | None = Header(None)) -> dict:
    """Extract and verify JWT from Authorization header.

    Returns: {user_id, google_sub, email}

    Emits 401 UNAUTHENTICATED (not FastAPI's default 422) when the header
    is missing, so clients can distinguish "please log in" from a genuine
    request-shape bug. See issue #5 — public endpoints behind this dep
    used to show "連線錯誤 422" to logged-out users.
    """
    if not authorization:
        raise HTTPException(401, "Missing authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid authorization header")

    token = authorization[7:]
    try:
        payload = jwt.decode(token, config.JWT_SECRET,
                             algorithms=[config.JWT_ALGORITHM])
        return {
            "user_id": payload["user_id"],
            "google_sub": payload["google_sub"],
            "email": payload["email"],
        }
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
