"""Community equipment endpoints."""
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from server.auth.deps import get_current_user
from server.db.engine import get_db
from server.equipment.models import SubmitRequest, VALID_SLOTS, VALID_RARITIES
from server.equipment import service
from sentinel.wallet.client import WalletError

router = APIRouter(prefix="/equipment", tags=["equipment"])


@router.post("/submit")
async def submit(req: SubmitRequest, user: dict = Depends(get_current_user)):
    """Submit a new community equipment template."""
    # Validate
    err = service.validate_submission(req.name, req.slot, req.rarity, req.buff)
    if err:
        raise HTTPException(400, err)

    if not await service.check_name_unique(req.name):
        raise HTTPException(409, f"Name '{req.name}' already exists")

    if not await service.check_daily_limit(user["user_id"]):
        raise HTTPException(429, "Daily submission limit reached")

    result = await service.create_submission(
        user["user_id"], req.name, req.slot, req.rarity,
        req.visual, req.buff, req.description, req.image_id,
    )
    return result


@router.get("/submissions")
async def list_submissions(
    status: str = Query("pending"),
    slot: str = Query(""),
    rarity: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
    user: dict | None = Depends(get_current_user),
):
    """List equipment submissions with optional filters."""
    db = await get_db()

    conditions = ["s.status = ?"]
    params: list = [status]

    if slot and slot in VALID_SLOTS:
        conditions.append("s.slot = ?")
        params.append(slot)
    if rarity and rarity in VALID_RARITIES:
        conditions.append("s.rarity = ?")
        params.append(rarity)

    where = " AND ".join(conditions)
    offset = (page - 1) * per_page
    params.extend([per_page, offset])

    rows = await db.execute_fetchall(
        f"SELECT s.*, u.display_name as creator_name "
        f"FROM equipment_submissions s "
        f"JOIN users u ON u.id = s.creator_id "
        f"WHERE {where} "
        f"ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
        params,
    )

    items = []
    for row in rows:
        item = dict(row)
        item["buff"] = json.loads(item["buff"]) if item["buff"] else None
        item["image_url"] = f"/images/{item['image_id']}" if item["image_id"] else ""

        # Check if current user voted
        if user:
            vote = await db.execute_fetchone(
                "SELECT 1 FROM votes WHERE user_id = ? AND submission_id = ?",
                (user["user_id"], item["id"]),
            )
            item["user_voted"] = bool(vote)
        else:
            item["user_voted"] = False

        items.append(item)

    # Total count
    count_row = await db.execute_fetchone(
        f"SELECT COUNT(*) as total FROM equipment_submissions s WHERE {where}",
        params[:-2],  # exclude LIMIT/OFFSET params
    )

    return {
        "items": items,
        "total": count_row["total"],
        "page": page,
        "per_page": per_page,
    }


@router.get("/submissions/{submission_id}")
async def get_submission(submission_id: str):
    """Get a single submission detail."""
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT s.*, u.display_name as creator_name "
        "FROM equipment_submissions s "
        "JOIN users u ON u.id = s.creator_id "
        "WHERE s.id = ?",
        (submission_id,),
    )
    if not row:
        raise HTTPException(404, "Submission not found")

    item = dict(row)
    item["buff"] = json.loads(item["buff"]) if item["buff"] else None
    item["image_url"] = f"/images/{item['image_id']}" if item["image_id"] else ""
    return item


@router.post("/submissions/{submission_id}/vote")
async def vote(submission_id: str, user: dict = Depends(get_current_user)):
    """Vote on a submission (costs 10 points)."""
    try:
        result = await service.cast_vote(user["user_id"], submission_id)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except WalletError as e:
        if e.is_insufficient_balance:
            raise HTTPException(402, "Insufficient balance")
        raise HTTPException(502, f"Wallet error: {e.message}")


@router.get("/pool")
async def get_pool(since_version: int = Query(0)):
    """Get community equipment pool (for client sync).

    Pass since_version to get only items added after that version.
    """
    db = await get_db()

    version_row = await db.execute_fetchone(
        "SELECT current_version FROM pool_sync WHERE id = 1"
    )
    current_version = version_row["current_version"] if version_row else 0

    if since_version >= current_version:
        return {"version": current_version, "items": []}

    rows = await db.execute_fetchall(
        "SELECT * FROM community_equipment WHERE version > ? ORDER BY version",
        (since_version,),
    )

    items = []
    for row in rows:
        item = dict(row)
        item["buff"] = json.loads(item["buff"]) if item["buff"] else None
        items.append(item)

    return {"version": current_version, "items": items}


@router.get("/pool/version")
async def get_pool_version():
    """Lightweight check for current pool version."""
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT current_version, updated_at FROM pool_sync WHERE id = 1"
    )
    return {
        "version": row["current_version"] if row else 0,
        "updated_at": row["updated_at"] if row else None,
    }
