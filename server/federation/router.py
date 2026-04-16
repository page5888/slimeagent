"""Federation HTTP endpoints — list patterns + vote.

Pattern submission (POST /federation/patterns) is intentionally absent
in this PR. See sentinel/growth/federation.py — the submission path
requires layer-2 abstraction + user approval UI, which ships later.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from server.auth.deps import get_current_user
from server.federation import service

router = APIRouter(prefix="/federation", tags=["federation"])


class VoteRequest(BaseModel):
    vote: str  # 'confirm' | 'refute' | 'unclear'


@router.get("/patterns")
async def list_patterns(
    limit: int = Query(20, ge=1, le=100),
    category: str | None = None,
    user: dict = Depends(get_current_user),
):
    """List recent patterns. Each item includes the caller's prior vote
    (or null if they haven't voted) so the UI can disable buttons.
    """
    items = await service.list_recent(
        limit=limit, category=category, user_id=user["user_id"],
    )
    return {"items": items, "count": len(items)}


@router.post("/patterns/{pattern_id}/vote")
async def vote_pattern(
    pattern_id: str,
    req: VoteRequest,
    user: dict = Depends(get_current_user),
):
    """Cast a vote on a pattern. Each user may vote once per pattern."""
    try:
        result = await service.cast_vote(user["user_id"], pattern_id, req.vote)
    except ValueError as e:
        msg = str(e)
        # Distinguish "not found" from other validation errors so the
        # client can react differently (e.g. refresh the list if the
        # pattern no longer exists).
        if "not found" in msg.lower():
            raise HTTPException(404, msg)
        raise HTTPException(400, msg)

    return {"ok": True, **result}
