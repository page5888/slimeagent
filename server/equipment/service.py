"""Equipment submission + voting + auto-approval logic."""
import json
import uuid
import logging
from server import config
from server.db.engine import get_db
from server.wallet.service import spend_points
from server.equipment.models import (
    VALID_SLOTS, VALID_RARITIES, MAX_BUFF_VALUES,
)
from sentinel.wallet.market_rules import SPEND_TYPE_CREATOR_REWARD

log = logging.getLogger("server.equipment")

# Pending submissions that don't reach their vote threshold within this
# many days are auto-expired. Keeps the queue fresh and prevents stale
# cards from crowding the vote tab forever.
SUBMISSION_EXPIRY_DAYS = 14


def validate_submission(name: str, slot: str, rarity: str,
                        buff: dict | None) -> str | None:
    """Validate submission fields. Returns error message or None."""
    if not name or len(name) > 30:
        return "Name must be 1-30 characters"
    if slot not in VALID_SLOTS:
        return f"Invalid slot: {slot}"
    if rarity not in VALID_RARITIES:
        return f"Invalid rarity: {rarity}"

    if buff:
        caps = MAX_BUFF_VALUES.get(rarity, {})
        for key, val in buff.items():
            if key not in caps:
                return f"Unknown buff type: {key}"
            if not isinstance(val, (int, float)) or val < 0:
                return f"Buff {key} must be a positive number"
            if val > caps[key]:
                return f"Buff {key} exceeds max for {rarity}: {val} > {caps[key]}"

    return None


async def check_name_unique(name: str) -> bool:
    """Check name is unique across built-in pool and community pool."""
    db = await get_db()
    # Check community_equipment
    row = await db.execute_fetchone(
        "SELECT 1 FROM community_equipment WHERE name = ?", (name,)
    )
    if row:
        return False
    # Check pending submissions too
    row = await db.execute_fetchone(
        "SELECT 1 FROM equipment_submissions WHERE name = ? AND status = 'pending'",
        (name,),
    )
    if row:
        return False

    # Check built-in pool
    import sys
    from pathlib import Path
    project_root = str(Path(__file__).parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from sentinel.wallet.equipment import EQUIPMENT_POOL
    for item in EQUIPMENT_POOL:
        if item["name"] == name:
            return False

    return True


async def check_daily_limit(user_id: str) -> bool:
    """Check if user hasn't exceeded daily submission limit."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT COUNT(*) as cnt FROM equipment_submissions "
        "WHERE creator_id = ? AND created_at > ?",
        (user_id, cutoff),
    )
    return row["cnt"] < config.MAX_SUBMISSIONS_PER_DAY


async def expire_stale_submissions() -> int:
    """Mark pending submissions older than SUBMISSION_EXPIRY_DAYS as expired.

    Called lazily from the /submissions listing endpoint so we don't need
    a separate cron job. Returns the number of submissions expired on
    this call (0 most of the time).
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=SUBMISSION_EXPIRY_DAYS)
    ).isoformat()
    db = await get_db()

    # Count first so we can log and return an accurate number.
    row = await db.execute_fetchone(
        "SELECT COUNT(*) as cnt FROM equipment_submissions "
        "WHERE status = 'pending' AND created_at < ?",
        (cutoff,),
    )
    count = row["cnt"] if row else 0
    if count == 0:
        return 0

    await db.execute(
        "UPDATE equipment_submissions SET status = 'expired' "
        "WHERE status = 'pending' AND created_at < ?",
        (cutoff,),
    )
    await db.commit()
    log.info("Expired %d stale submissions (older than %d days)",
             count, SUBMISSION_EXPIRY_DAYS)
    return count


async def create_submission(user_id: str, name: str, slot: str, rarity: str,
                            visual: str, buff: dict | None,
                            description: str, image_id: str | None) -> dict:
    """Create a new equipment submission."""
    sub_id = str(uuid.uuid4())
    threshold = config.VOTE_THRESHOLDS.get(rarity, 10)

    db = await get_db()
    await db.execute(
        "INSERT INTO equipment_submissions "
        "(id, creator_id, name, slot, rarity, visual, buff, description, "
        "image_id, vote_threshold) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sub_id, user_id, name, slot, rarity, visual,
         json.dumps(buff) if buff else None,
         description, image_id, threshold),
    )
    await db.commit()

    return {
        "id": sub_id,
        "name": name,
        "slot": slot,
        "rarity": rarity,
        "vote_threshold": threshold,
        "status": "pending",
    }


async def cast_vote(user_id: str, submission_id: str) -> dict:
    """Cast a vote on a submission. Costs VOTE_COST points.

    Returns: {vote_count, approved, submission_name}
    """
    db = await get_db()

    # Check submission exists and is pending
    sub = await db.execute_fetchone(
        "SELECT id, creator_id, name, slot, rarity, visual, buff, description, "
        "image_id, vote_count, vote_threshold, status "
        "FROM equipment_submissions WHERE id = ?",
        (submission_id,),
    )
    if not sub:
        raise ValueError("Submission not found")
    if sub["status"] != "pending":
        raise ValueError("Submission is not open for voting")
    if sub["creator_id"] == user_id:
        raise ValueError("Cannot vote on your own submission")

    # Check not already voted
    existing = await db.execute_fetchone(
        "SELECT 1 FROM votes WHERE user_id = ? AND submission_id = ?",
        (user_id, submission_id),
    )
    if existing:
        raise ValueError("Already voted on this submission")

    # Deduct points. Reason must be slime_creator_reward — 5888
    # sitePolicy rejects anything else with 403 SITE_NOT_AUTHORIZED.
    # Idempotency key is scoped to the vote_id so 5888 dedupes client
    # retries safely.
    vote_id = str(uuid.uuid4())
    idempotency_key = f"slime_creator_reward:{vote_id}"
    await spend_points(user_id, config.VOTE_COST,
                       SPEND_TYPE_CREATOR_REWARD, idempotency_key)

    # Phase 1 ledger entry — creator will be paid in a future batch
    # once s2sCreatorRewardSettle ships. See 003_creator_ledger.sql.
    ledger_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO creator_reward_ledger "
        "(id, creator_id, voter_id, submission_id, amount, voter_spend_key) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ledger_id, sub["creator_id"], user_id, submission_id,
         config.VOTE_COST, idempotency_key),
    )

    # Record vote
    await db.execute(
        "INSERT INTO votes (id, user_id, submission_id) VALUES (?, ?, ?)",
        (vote_id, user_id, submission_id),
    )
    new_count = sub["vote_count"] + 1
    await db.execute(
        "UPDATE equipment_submissions SET vote_count = ? WHERE id = ?",
        (new_count, submission_id),
    )

    # Check threshold
    approved = False
    if new_count >= sub["vote_threshold"]:
        approved = await _approve_submission(db, sub)

    await db.commit()

    return {
        "vote_count": new_count,
        "vote_threshold": sub["vote_threshold"],
        "approved": approved,
        "submission_name": sub["name"],
    }


async def _approve_submission(db, sub) -> bool:
    """Auto-approve a submission that reached vote threshold."""
    submission_id = sub["id"]

    # Update status
    await db.execute(
        "UPDATE equipment_submissions SET status = 'approved', "
        "approved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (submission_id,),
    )

    # Build image URL
    image_url = f"/images/{sub['image_id']}" if sub["image_id"] else ""

    # Insert into community_equipment
    # Get next version
    row = await db.execute_fetchone(
        "SELECT current_version FROM pool_sync WHERE id = 1"
    )
    new_version = (row["current_version"] if row else 0) + 1

    await db.execute(
        "INSERT INTO community_equipment "
        "(id, name, slot, rarity, visual, buff, description, image_url, "
        "creator_id, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (submission_id, sub["name"], sub["slot"], sub["rarity"],
         sub["visual"], sub["buff"], sub["description"],
         image_url, sub["creator_id"], new_version),
    )

    await db.execute(
        "UPDATE pool_sync SET current_version = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = 1",
        (new_version,),
    )

    # Creator approval bonus — Phase 1: record in ledger, NOT granted
    # via 5888 wallet yet. Phase 2 (ready as of 2026-04-16) will pay
    # these via s2sGrant(reason=slime_creator_approval) from the replay
    # script — that reason is now on the 5888 staging grant whitelist.
    # Dedicated ledger entry with voter_id=NULL and spend_key prefix
    # "slime_creator_approval:" so the replay can route it to the
    # right reason (vs per-vote tips, which use slime_creator_reward_settle).
    bonus_id = str(uuid.uuid4())
    bonus_key = f"slime_creator_approval:{submission_id}"
    await db.execute(
        "INSERT INTO creator_reward_ledger "
        "(id, creator_id, voter_id, submission_id, amount, voter_spend_key) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (bonus_id, sub["creator_id"], None, submission_id,
         config.CREATOR_REWARD, bonus_key),
    )

    log.info(f"Submission approved: [{sub['rarity']}] {sub['name']} "
             f"(slot={sub['slot']}, votes={sub['vote_count'] + 1}); "
             f"creator {sub['creator_id']} owed {config.CREATOR_REWARD} pts "
             f"in ledger (pending Phase 2 settle)")
    return True
