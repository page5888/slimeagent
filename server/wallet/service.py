"""Wallet service — wraps 5888 WalletClient for server use."""
import sys
from pathlib import Path
from server import config

# Add project root so we can import sentinel.wallet.client
_project_root = str(Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from sentinel.wallet.client import WalletClient, WalletError

_client: WalletClient | None = None


def get_wallet_client() -> WalletClient | None:
    """Get singleton WalletClient. Returns None if not configured."""
    global _client
    if _client is not None:
        return _client
    if not config.WALLET_API_BASE or not config.WALLET_HMAC_SECRET:
        return None
    _client = WalletClient(
        api_base=config.WALLET_API_BASE,
        site_id=config.WALLET_SITE_ID,
        api_key=config.WALLET_API_KEY,
        hmac_secret=config.WALLET_HMAC_SECRET,
    )
    return _client


async def get_wallet_uid(user_id: str) -> str:
    """Look up wallet_uid from users table."""
    from server.db.engine import get_db
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT wallet_uid FROM users WHERE id = ?", (user_id,)
    )
    return row["wallet_uid"] if row and row["wallet_uid"] else ""


async def spend_points(user_id: str, amount: int, reason: str,
                       idempotency_key: str) -> dict:
    """Deduct points from user's 5888 wallet.

    Raises WalletError on failure.
    """
    wc = get_wallet_client()
    if not wc:
        raise WalletError(503, "WALLET_NOT_CONFIGURED", "5888 wallet not configured")
    uid = await get_wallet_uid(user_id)
    if not uid:
        raise WalletError(400, "NO_WALLET", "User has no wallet linked")
    return wc.spend(uid, amount, reason, idempotency_key)


async def grant_points(user_id: str, amount: int, reason: str,
                       idempotency_key: str) -> dict:
    """Grant points to user's 5888 wallet."""
    wc = get_wallet_client()
    if not wc:
        raise WalletError(503, "WALLET_NOT_CONFIGURED", "5888 wallet not configured")
    uid = await get_wallet_uid(user_id)
    if not uid:
        raise WalletError(400, "NO_WALLET", "User has no wallet linked")
    return wc.grant(uid, amount, reason, idempotency_key)
