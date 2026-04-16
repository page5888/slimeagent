"""Relay server configuration — loaded from environment variables."""
import os
from pathlib import Path

# ── Server ───────────────────────────────────────────────────────────
HOST = os.getenv("RELAY_HOST", "0.0.0.0")
PORT = int(os.getenv("RELAY_PORT", "8000"))
DEBUG = os.getenv("RELAY_DEBUG", "0") == "1"

# ── Database ─────────────────────────────────────────────────────────
DB_PATH = Path(os.getenv("RELAY_DB_PATH", str(Path(__file__).parent / "relay.db")))

# ── JWT ──────────────────────────────────────────────────────────────
JWT_SECRET = os.getenv("RELAY_JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24 * 7  # 7 days

# ── Google OAuth ─────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

# ── 5888 Wallet S2S ─────────────────────────────────────────────────
# Priority: env vars > ~/.hermes/wallet_5888_keys.json (local dev only) > empty.
# WALLET_ENV selects which block to load from the JSON; defaults to "staging".
# Production always requires env-var overrides — the JSON is a dev convenience.
WALLET_API_BASE = os.getenv("WALLET_API_BASE", "")
WALLET_SITE_ID = os.getenv("WALLET_SITE_ID", "")
WALLET_API_KEY = os.getenv("WALLET_API_KEY", "")
WALLET_HMAC_SECRET = os.getenv("WALLET_HMAC_SECRET", "")


def _load_wallet_keys_from_home():
    """Fall back to ~/.hermes/wallet_5888_keys.json when env vars are empty.

    Convenience for local dev only. The file is never committed — it lives
    in the user's home dir and holds staging/production secrets.
    """
    global WALLET_API_BASE, WALLET_SITE_ID, WALLET_API_KEY, WALLET_HMAC_SECRET
    # Skip if already configured via env
    if WALLET_API_BASE and WALLET_HMAC_SECRET:
        return
    keys_file = Path.home() / ".hermes" / "wallet_5888_keys.json"
    if not keys_file.exists():
        return
    import json
    try:
        data = json.loads(keys_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    env = os.getenv("WALLET_ENV", "staging")
    block = data.get(env) or {}
    WALLET_API_BASE = WALLET_API_BASE or block.get("base_url", "")
    WALLET_SITE_ID = WALLET_SITE_ID or block.get("site_id", "")
    WALLET_API_KEY = WALLET_API_KEY or block.get("api_key", "")
    WALLET_HMAC_SECRET = WALLET_HMAC_SECRET or block.get("hmac_secret", "")


_load_wallet_keys_from_home()

# ── Images ───────────────────────────────────────────────────────────
UPLOAD_DIR = Path(os.getenv("RELAY_UPLOAD_DIR", str(Path(__file__).parent / "uploads")))
MAX_IMAGE_SIZE = 512 * 1024  # 512 KB
MAX_IMAGE_DIMENSION = 256    # pixels

# ── Voting ───────────────────────────────────────────────────────────
VOTE_COST = 10  # points per vote

VOTE_THRESHOLDS = {
    "common": 5,
    "uncommon": 5,
    "rare": 10,
    "epic": 10,
    "legendary": 20,
    "mythic": 20,
    "ultimate": 30,
}

CREATOR_REWARD = 100  # bonus points when submission is approved

# ── Marketplace ──────────────────────────────────────────────────────
BASE_FEE_PERCENT = 10  # platform takes 10%
MIN_FEE_PERCENT = 3    # floor after equipment discounts

# ── Submission limits ────────────────────────────────────────────────
MAX_SUBMISSIONS_PER_DAY = 3
