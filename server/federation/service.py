"""Federation DB operations: list patterns, record a vote, prevent double-vote.

Promotion logic (once votes_confirm hits a threshold, flip status to
'community') is kept server-side so clients can't game it. The
threshold is simple for v1 — 5 confirms and confirms > refutes × 2.
Real tuning comes after we have actual submission data.
"""
import uuid
import logging
from server.db.engine import get_db

log = logging.getLogger("server.federation")

# Vote tokens exposed in the API. Anything else → 400.
VALID_VOTES = {"confirm", "refute", "unclear"}

# Promotion threshold — kept deliberately low so the demo seeds can
# promote after a handful of votes. Will be raised once real patterns
# start flowing.
PROMOTE_CONFIRM_MIN = 5
PROMOTE_CONFIRM_RATIO = 2  # confirms must be >= refutes * this


async def list_recent(limit: int = 20,
                      category: str | None = None,
                      user_id: str | None = None) -> list[dict]:
    """Return recent patterns in vote-eligible state.

    When `user_id` is provided, each pattern gets a `user_voted` field
    with the user's prior vote ('confirm'/'refute'/'unclear') or None.
    This lets the UI show which patterns the user has already voted on
    so they can't double-vote and the buttons can grey out.
    """
    db = await get_db()
    # Show both pending and community (promoted) — users can still
    # refute even after promotion; that's how bad patterns get demoted.
    params: list = []
    where = "status IN ('pending', 'community')"
    if category:
        where += " AND category = ?"
        params.append(category)
    params.append(limit)

    rows = await db.execute_fetchall(
        f"SELECT id, category, statement, confidence, sample_n, status, "
        f"votes_confirm, votes_refute, votes_unclear, promoted_at, created_at "
        f"FROM patterns WHERE {where} ORDER BY created_at DESC LIMIT ?",
        params,
    )

    if user_id and rows:
        # Fetch this user's votes for the returned patterns in one query
        pattern_ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(pattern_ids))
        voted_rows = await db.execute_fetchall(
            f"SELECT pattern_id, vote FROM pattern_votes "
            f"WHERE user_id = ? AND pattern_id IN ({placeholders})",
            [user_id] + pattern_ids,
        )
        voted_map = {r["pattern_id"]: r["vote"] for r in voted_rows}
        for r in rows:
            r["user_voted"] = voted_map.get(r["id"])
    else:
        for r in rows:
            r["user_voted"] = None

    return rows


async def cast_vote(user_id: str, pattern_id: str, vote: str) -> dict:
    """Record a vote. Raises ValueError on already-voted or not-found.

    Returns: {pattern_id, vote, totals: {confirm, refute, unclear}, promoted}
    """
    if vote not in VALID_VOTES:
        raise ValueError(f"Invalid vote '{vote}' — must be one of {VALID_VOTES}")

    db = await get_db()

    pat = await db.execute_fetchone(
        "SELECT id, status, votes_confirm, votes_refute, votes_unclear, "
        "creator_id FROM patterns WHERE id = ?",
        (pattern_id,),
    )
    if not pat:
        raise ValueError("Pattern not found")
    if pat["status"] not in ("pending", "community"):
        # e.g. 'rejected' or 'deleted' — stop accepting votes
        raise ValueError("Pattern is not accepting votes")
    if pat.get("creator_id") and pat["creator_id"] == user_id:
        raise ValueError("Cannot vote on your own pattern")

    # Check not already voted — the UNIQUE constraint would also catch
    # this, but a clean error beats a DB integrity crash.
    existing = await db.execute_fetchone(
        "SELECT vote FROM pattern_votes WHERE user_id = ? AND pattern_id = ?",
        (user_id, pattern_id),
    )
    if existing:
        raise ValueError("Already voted on this pattern")

    # Insert the vote + bump the counter in one pass. Not a real
    # transaction (aiosqlite + asyncpg here behave differently), but
    # the UNIQUE constraint guarantees at most one vote row per
    # (user, pattern) even under race.
    vote_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO pattern_votes (id, user_id, pattern_id, vote) "
        "VALUES (?, ?, ?, ?)",
        (vote_id, user_id, pattern_id, vote),
    )

    col = {
        "confirm": "votes_confirm",
        "refute": "votes_refute",
        "unclear": "votes_unclear",
    }[vote]
    await db.execute(
        f"UPDATE patterns SET {col} = {col} + 1 WHERE id = ?",
        (pattern_id,),
    )

    # Re-read to get updated counts + decide on promotion
    updated = await db.execute_fetchone(
        "SELECT status, votes_confirm, votes_refute, votes_unclear "
        "FROM patterns WHERE id = ?",
        (pattern_id,),
    )

    promoted = False
    if (updated["status"] == "pending"
            and updated["votes_confirm"] >= PROMOTE_CONFIRM_MIN
            and updated["votes_confirm"] >= max(1, updated["votes_refute"]) * PROMOTE_CONFIRM_RATIO):
        await db.execute(
            "UPDATE patterns SET status = 'community', "
            "promoted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pattern_id,),
        )
        promoted = True
        log.info(f"Pattern {pattern_id} promoted to community knowledge")

    await db.commit()

    return {
        "pattern_id": pattern_id,
        "vote": vote,
        "totals": {
            "confirm": updated["votes_confirm"],
            "refute": updated["votes_refute"],
            "unclear": updated["votes_unclear"],
        },
        "promoted": promoted,
    }
