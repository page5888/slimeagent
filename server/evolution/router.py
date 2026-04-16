"""Evolution payment endpoints.

The desktop app holds the actual evolution state (per-user local file);
this endpoint only handles the 2-pt fee deduction via 5888. The
desktop is responsible for calling `perform_evolution()` locally
AFTER a successful response here.

Flow:
  1. Desktop checks is_evolution_available() locally
  2. Desktop POSTs /evolution/evolve with idempotency_key
  3. Relay deducts 2 pts via spend_points(slime_evolve)
  4. Relay returns {ok: True, balance_after, ...} or error
  5. Desktop calls perform_evolution() locally on success

BYOK users should NOT hit this endpoint — the desktop branches to
free evolution when user_mode == 'byok'.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server.auth.deps import get_current_user
from server.wallet.service import spend_points
from sentinel.wallet.client import WalletError
from sentinel.wallet.market_rules import EVOLVE_COST, SPEND_TYPE_EVOLVE

router = APIRouter(prefix="/evolution", tags=["evolution"])


class EvolveRequest(BaseModel):
    # Client-supplied idempotency key so the same evolution attempt
    # dedupes on 5888 side. Format: "slime_evolve_<user_uuid>_<attempt_uuid>".
    # If absent, we generate one — but the client SHOULD pass one so
    # retries after network failure don't double-charge.
    idempotency_key: str | None = None


@router.post("/evolve")
async def evolve(req: EvolveRequest,
                 user: dict = Depends(get_current_user)):
    """Deduct 2 pts for a manual evolution trigger.

    Returns on success:
      {"ok": true, "cost": 2, "balance_after": int, "idempotency_key": str}

    Raises:
      402 if insufficient balance
      502 if wallet is unreachable / misconfigured
    """
    key = req.idempotency_key or f"slime_evolve_{user['user_id']}_{uuid.uuid4()}"

    try:
        result = await spend_points(
            user["user_id"], EVOLVE_COST, SPEND_TYPE_EVOLVE, key,
        )
    except WalletError as e:
        if e.is_insufficient_balance:
            raise HTTPException(
                402,
                f"Insufficient balance — need {EVOLVE_COST} pts to evolve",
            )
        # Not configured / network / 5xx → bubble up as bad gateway
        raise HTTPException(502, f"Wallet error: {e.message}")

    return {
        "ok": True,
        "cost": EVOLVE_COST,
        "balance_after": result.get("balanceAfter"),
        "idempotency_key": key,
    }
