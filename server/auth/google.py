"""Verify Google OAuth id_token."""
import httpx
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from server import config


async def verify_google_token(token: str) -> dict:
    """Verify Google id_token and return user info.

    Returns: {sub, email, name, picture}
    """
    info = id_token.verify_oauth2_token(
        token, google_requests.Request(), config.GOOGLE_CLIENT_ID
    )
    return {
        "sub": info["sub"],
        "email": info.get("email", ""),
        "name": info.get("name", ""),
        "picture": info.get("picture", ""),
    }
