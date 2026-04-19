"""Federation HTTP endpoints — list patterns, submit, and vote.

Submission was stubbed for the early design-only phase; it now ships
as of Phase A1. See sentinel/growth/federation.py for the three-layer
design (local → pattern abstraction → community voting). This router
only handles the HTTP seam; all rule enforcement (PII scrub, rate
limit, category whitelist, length cap) lives in service.submit_pattern
so CLI scripts or tests can hit the same gates.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server.auth.deps import get_current_user
from server.federation import service

router = APIRouter(prefix="/federation", tags=["federation"])


class VoteRequest(BaseModel):
    vote: str  # 'confirm' | 'refute' | 'unclear'


class SubmitPatternRequest(BaseModel):
    category: str
    statement: str = Field(..., min_length=1, max_length=200)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    sample_n: int = Field(1, ge=1)


# Map service-layer rejection codes to HTTP status. Kept here (not in
# the service) so the error taxonomy is a router concern; the service
# just classifies what went wrong.
_REJECT_STATUS = {
    "INVALID_CATEGORY":   400,
    "EMPTY_STATEMENT":    400,
    "STATEMENT_TOO_LONG": 400,
    "PII_DETECTED":       422,  # shape is valid, content isn't
    "RATE_LIMITED":       429,
}


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


@router.post("/patterns")
async def submit_pattern(
    req: SubmitPatternRequest,
    user: dict = Depends(get_current_user),
):
    """Submit a new pattern to the community pool.

    The server enforces PII scrub, per-user daily rate limit, category
    whitelist, and length cap. Rejected submissions return a structured
    error code (e.g. PII_DETECTED, RATE_LIMITED) so the client can show
    a targeted message instead of "400 bad request".
    """
    try:
        result = await service.submit_pattern(
            user_id=user["user_id"],
            category=req.category,
            statement=req.statement,
            confidence=req.confidence,
            sample_n=req.sample_n,
        )
    except service.SubmitRejected as e:
        status = _REJECT_STATUS.get(e.code, 400)
        raise HTTPException(
            status,
            detail={"code": e.code, "message": str(e)},
        )
    return {"ok": True, **result}


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
