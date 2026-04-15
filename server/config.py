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
WALLET_API_BASE = os.getenv("WALLET_API_BASE", "")
WALLET_SITE_ID = os.getenv("WALLET_SITE_ID", "")
WALLET_API_KEY = os.getenv("WALLET_API_KEY", "")
WALLET_HMAC_SECRET = os.getenv("WALLET_HMAC_SECRET", "")

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
