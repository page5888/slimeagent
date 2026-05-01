"""Federation DB operations: list patterns, record a vote, prevent double-vote.

Promotion logic (once votes_confirm hits a threshold, flip status to
'community') is kept server-side so clients can't game it. The
threshold is simple for v1 — 5 confirms and confirms > refutes × 2.
Real tuning comes after we have actual submission data.
"""
import re
import uuid
import logging
from datetime import datetime, timedelta, timezone
from server.db.engine import get_db

log = logging.getLogger("server.federation")

# Vote tokens exposed in the API. Anything else → 400.
VALID_VOTES = {"confirm", "refute", "unclear"}

# Promotion threshold — kept deliberately low so the demo seeds can
# promote after a handful of votes. Will be raised once real patterns
# start flowing.
PROMOTE_CONFIRM_MIN = 5
PROMOTE_CONFIRM_RATIO = 2  # confirms must be >= refutes * this

# Submission side — keeps the pool signal-to-noise high without a
# moderation team. These mirror the design doc in
# sentinel/growth/federation.py.
VALID_CATEGORIES = frozenset({
    "schedule", "tooling", "workflow", "health", "focus",
})
MAX_STATEMENT_LEN = 100
DAILY_SUBMIT_LIMIT = 3  # per user per rolling 24h

# PII regexes — reject the whole statement if any match. Tuned to be
# strict on things we really don't want propagating ever (email, URL,
# absolute paths, long hex tokens) rather than trying to rewrite around
# them. The client can re-distill with a cleaner prompt.
_PII_PATTERNS = [
    # email
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "email"),
    # URLs (http/https/ftp)
    (re.compile(r"https?://\S+|ftp://\S+", re.IGNORECASE), "URL"),
    # Windows absolute paths (C:\, D:/...)
    (re.compile(r"[A-Za-z]:[/\\]"), "Windows path"),
    # POSIX absolute paths under common system roots
    (re.compile(r"(?:^|\s)/(?:Users|home|Volumes|tmp|var|etc|opt|usr|srv|root|mnt)/"), "POSIX path"),
    # Phone-shaped numbers (loose; trigger on 9+ digits in a row,
    # optionally separated by spaces/dashes)
    (re.compile(r"\b\d{3,4}[\s-]?\d{3,4}[\s-]?\d{3,4}\b"), "phone number"),
    # Long hex tokens (≥ 8 hex chars — catches git shas, session ids,
    # hashes). Short ones like "0x1A" stay allowed.
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "hex token"),
]


def _scan_pii(statement: str) -> str | None:
    """Return the first PII category matched, or None if clean."""
    for rx, label in _PII_PATTERNS:
        if rx.search(statement):
            return label
    return None


def _parse_timestamp(s: str) -> datetime:
    """Parse a DB timestamp string to aware UTC datetime.

    SQLite stores `CURRENT_TIMESTAMP` as 'YYYY-MM-DD HH:MM:SS' (naive UTC).
    Postgres returns ISO with tz. Handle both.
    """
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    # Postgres ISO form like '2026-04-19T10:23:45+00:00'
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        # SQLite form — treat as UTC
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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


async def list_user_patterns(user_id: str, limit: int = 50) -> list[dict]:
    """Return patterns this user has submitted, newest first.

    Used by the 「🏆 我的貢獻」 dialog in the client so users can see how
    their shared patterns are doing. Includes status (pending /
    community / rejected) and the three vote counters, no need to join
    anything — all state is on the pattern row itself.
    """
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, category, statement, confidence, sample_n, status, "
        "votes_confirm, votes_refute, votes_unclear, promoted_at, created_at "
        "FROM patterns WHERE creator_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
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


# ── Submission ───────────────────────────────────────────────────────


class SubmitRejected(ValueError):
    """Raised when a pattern fails validation / rate-limit / PII scrub.

    Carries a stable `code` so the API layer can map it to a specific
    HTTP status and the client can show a targeted error (not just
    "400 bad request").
    """
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


async def submit_pattern(
    user_id: str,
    category: str,
    statement: str,
    confidence: float = 0.5,
    sample_n: int = 1,
) -> dict:
    """Insert a new pattern submitted by a user.

    Enforces:
      - category must be in VALID_CATEGORIES
      - statement length 1..MAX_STATEMENT_LEN
      - PII scrub (rejects email/URL/path/phone/long-hex anywhere)
      - per-user rate limit (DAILY_SUBMIT_LIMIT in the last 24h)

    Returns: {"id": str, "status": "pending", ...}

    Raises SubmitRejected on any rule hit. The caller (router) maps
    the `code` field to an HTTP status.
    """
    # ── Shape validation ────────────────────────────────────────────
    if category not in VALID_CATEGORIES:
        raise SubmitRejected(
            "INVALID_CATEGORY",
            f"Category must be one of {sorted(VALID_CATEGORIES)}",
        )

    statement = (statement or "").strip()
    if not statement:
        raise SubmitRejected("EMPTY_STATEMENT", "Statement cannot be empty")
    if len(statement) > MAX_STATEMENT_LEN:
        raise SubmitRejected(
            "STATEMENT_TOO_LONG",
            f"Statement must be ≤ {MAX_STATEMENT_LEN} chars (got {len(statement)})",
        )

    # Clamp numeric fields instead of rejecting — if the client is
    # slightly out of range (e.g. confidence 1.2 due to rounding) we
    # don't want to fail the submit, just normalize.
    confidence = max(0.0, min(1.0, float(confidence)))
    sample_n = max(1, int(sample_n))

    # ── PII scrub ───────────────────────────────────────────────────
    pii = _scan_pii(statement)
    if pii:
        raise SubmitRejected(
            "PII_DETECTED",
            f"Statement appears to contain a {pii}. Federation patterns "
            f"must be generalized — no identifiers, URLs, or file paths.",
        )

    # ── Rate limit ──────────────────────────────────────────────────
    # Fetch the most recent few rows from this user and count how many
    # fall inside the 24h window. Doing the windowing in Python keeps
    # this portable across SQLite and Postgres without dialect-specific
    # date math. DAILY_SUBMIT_LIMIT is small (3) so fetching 10 rows is
    # never a scale concern.
    db = await get_db()
    recent = await db.execute_fetchall(
        "SELECT created_at FROM patterns WHERE creator_id = ? "
        "ORDER BY created_at DESC LIMIT 10",
        (user_id,),
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    in_window = 0
    for row in recent:
        try:
            ts = _parse_timestamp(row["created_at"])
        except Exception:
            # If we can't parse, assume it's old and skip — safer than
            # blocking on a malformed row.
            continue
        if ts >= cutoff:
            in_window += 1
    if in_window >= DAILY_SUBMIT_LIMIT:
        raise SubmitRejected(
            "RATE_LIMITED",
            f"You've already submitted {in_window} patterns in the last 24 hours "
            f"(limit is {DAILY_SUBMIT_LIMIT}). Try again tomorrow.",
        )

    # ── Insert ──────────────────────────────────────────────────────
    pattern_id = f"pat_{uuid.uuid4().hex[:12]}"
    await db.execute(
        "INSERT INTO patterns (id, category, statement, confidence, sample_n, "
        "status, creator_id) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (pattern_id, category, statement, confidence, sample_n, user_id),
    )
    await db.commit()
    log.info(f"Pattern {pattern_id} submitted by user {user_id} "
             f"(category={category}, len={len(statement)})")

    return {
        "id": pattern_id,
        "category": category,
        "statement": statement,
        "confidence": confidence,
        "sample_n": sample_n,
        "status": "pending",
        "remaining_today": DAILY_SUBMIT_LIMIT - in_window - 1,
    }
