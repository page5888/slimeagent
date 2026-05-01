"""Federation — how slimes learn from each other without leaking data.

STATUS: DESIGN ONLY. Nothing in this file makes network calls. The
functions are stubs that return empty lists / refuse submissions. The
design below is what PR 5 will implement.

Why this is a design doc in code form instead of a markdown file:
future contributors (and future-me) will read imports before docs.
Putting the shape of the API here means the rest of the codebase can
reference federation.submit_pattern() at its final signature today,
even though the implementation will arrive later.

================================================================
THE PROBLEM
================================================================

Peter asked: 跨文化可以學 但是要怎麼學

It's tempting to ship "all slimes share memories". It's a disaster:
  - Privacy: one user's activity leaks to another.
  - Correlation: patterns from user A may be entirely wrong for user B.
  - Abuse: a malicious slime can poison the shared pool.
  - Scale: raw events won't fit or survive aggregation.

================================================================
THE DESIGN — three layers, each with a hard gate
================================================================

LAYER 1: LOCAL OBSERVATION (today, already exists)
    Slime sees raw events: window titles, file paths, process names.
    These NEVER leave the user's machine. No path in this file reads
    them directly.

LAYER 2: PATTERN ABSTRACTION (new, user opt-in per pattern)
    Slime extracts a *pattern*: a generalized observation with all
    user-specific bits stripped out. Example:

      raw:      "VS Code opened D:/srbow_bots/ai-slime-agent/... at 23:47"
      pattern:  "this user tends to open their primary IDE after 23:00"

    Patterns have a fixed schema (below) and are reviewed by the user
    before submission. The UI will show:

      「我觀察到一個模式：[你在深夜常工作]
       要分享給社群嗎？只有描述會上傳，原始紀錄不會。」

    Patterns are submitted anonymously. The server never gets user_id,
    device_id, or any identifier that could be linked back.

LAYER 3: COMMUNITY VOTING (other slimes confirm or deny)
    Submitted patterns go into a public pool on the relay server.
    Other users' slimes fetch recent patterns and vote:
      - "✓ confirmed" — my user shows this too
      - "✗ refuted"   — my user does not show this
      - "? unclear"   — I can't tell
    A pattern needs N confirmations from distinct slimes to become
    *community knowledge*. Low-quality patterns decay and are purged.

    When a pattern becomes community knowledge, any slime can apply it
    — but only as a HYPOTHESIS, never a fact. The slime says:

      「社群觀察到『深夜工作者多半會在早上 10 點才起』
       你也是這樣嗎？」

    If the user confirms, it becomes part of that slime's local model
    of its user. If denied, the slime notes "my user is an exception
    to [pattern]" and never raises it again.

================================================================
PATTERN SCHEMA
================================================================

A Pattern is a piece of generalized knowledge. Shape:

    {
      "id": "pat_<short-hash>",
      "category": "schedule" | "tooling" | "health" | "workflow" | ...,
      "statement": "<plain text, under 100 chars, no PII>",
      "confidence_local": 0.0 to 1.0,  // how sure the proposing slime is
      "sample_n": <int>,               // how many observations support it
      "submitted_at": <unix ts>,
      "votes_confirm": <int>,
      "votes_refute": <int>,
      "promoted_at": <unix ts | null>, // when it became community knowledge
    }

Hard limits (enforced server-side in PR 5):
  - statement length ≤ 100 chars
  - statement must not contain: absolute paths, email addresses, URLs,
    phone-number-shaped strings, any identifier > 6 chars of hex.
  - categories are a fixed enum; free-form category names are rejected.

================================================================
SAFETY AGAINST ABUSE
================================================================

1. Rate limit: each slime can submit ≤ 3 patterns per day.
2. PII filter: server-side regex scrub. Anything with a file path,
   URL, email, or high-entropy token is auto-refused with a clear
   error ("contained something that looked like an email address").
3. Voting integrity: a slime cannot vote on its own patterns. A slime
   can vote on each pattern at most once.
4. User opt-in: no pattern is submitted without explicit per-pattern
   user approval via the GUI. Global "share everything" switch does
   not exist — too easy to forget it's on.
5. User opt-out: users can delete any pattern they submitted. The
   pattern is removed from the pool and its votes zeroed.
6. Bidirectional mute: users can block specific pattern categories
   from ever being applied to their slime ("I don't want health
   advice, only workflow hints").

================================================================
WHAT THIS FILE PROVIDES TODAY
================================================================

The API shape below exists so callers can write against it now. The
implementations are inert — they log a warning and return defaults.
PR 5 will flesh out the HTTP calls to the relay.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.growth.federation")

# Local queue of pattern candidates the slime wants to share. The user
# reviews these in the 公頻 tab and explicitly approves each before it
# hits the network. Size is capped so a runaway distiller can't balloon
# the file.
PENDING_FILE = Path.home() / ".hermes" / "pending_federation.json"
MAX_PENDING = 20

# Categories are intentionally few and concrete. Adding a new one is a
# schema change that needs server-side update too.
VALID_CATEGORIES = frozenset({
    "schedule",   # when the user tends to do things
    "tooling",    # which tools they favor
    "workflow",   # how steps chain together
    "health",     # physical state (break reminders, posture, etc.)
    "focus",      # attention / flow patterns
})


@dataclass
class Pattern:
    """A generalized, user-approved observation suitable for sharing."""
    id: str = ""
    category: str = ""
    statement: str = ""
    confidence_local: float = 0.0
    sample_n: int = 0
    submitted_at: float = 0.0
    votes_confirm: int = 0
    votes_refute: int = 0
    promoted_at: Optional[float] = None


@dataclass
class PatternSubmitError:
    """Why a submission was refused. Returned instead of raising so
    callers can present the reason to the user without a stack trace."""
    code: str
    message: str


# ── Submission (Phase A1 — live) ──────────────────────────────────

def submit_pattern(pattern: Pattern) -> Optional[PatternSubmitError]:
    """Submit a pattern to the community pool via the relay.

    Runs client-side validation first (saves a round-trip), then POSTs
    through relay_client. Server re-checks everything — the two gates
    are deliberately redundant so a malicious client can't skip scrub.

    Returns None on success, a PatternSubmitError on refusal. Callers
    should show the error.message directly to the user.
    """
    local_err = validate_pattern(pattern)
    if local_err is not None:
        return local_err

    try:
        # Imported lazily so tests that only exercise the pure helpers
        # above don't need a relay URL configured.
        from sentinel import relay_client
        resp = relay_client.submit_pattern(
            category=pattern.category,
            statement=pattern.statement,
            confidence=pattern.confidence_local,
            sample_n=pattern.sample_n,
        )
        # Server returned the assigned id — propagate so the caller can
        # track it (and eventually show in a "my contributions" tab).
        pattern.id = resp.get("id", pattern.id)
        pattern.submitted_at = time.time()
        log.info(f"Pattern submitted: id={pattern.id} category={pattern.category}")
        return None
    except Exception as e:
        # relay_client raises RelayError with .code set to HTTP status
        # or a tag ("NETWORK", "NOT_CONFIGURED"). Map the common cases
        # to design-doc codes so the UI can react consistently.
        code_raw = getattr(e, "code", "NETWORK")
        message = getattr(e, "message", str(e))
        mapped = {
            "401": ("NOT_AUTHED", "請先到「設定」分頁登入 Google 帳號。"),
            "422": ("CONTAINS_PII", f"伺服器拒絕：{message}"),
            "429": ("RATE_LIMITED", f"今天分享次數已達上限：{message}"),
            "400": ("BAD_REQUEST", f"伺服器拒絕：{message}"),
        }.get(str(code_raw), ("NETWORK", f"連線失敗：{message}"))
        log.warning(f"Pattern submit refused: {mapped[0]} — {mapped[1]}")
        return PatternSubmitError(code=mapped[0], message=mapped[1])


# Fetch helpers kept as stubs — the GUI calls relay_client directly for
# the list/vote views, so these client-side re-wrappings aren't used
# yet. Leaving them in place so the design doc at the top of this file
# still matches the public API surface.

def fetch_community_patterns(category: Optional[str] = None,
                             limit: int = 20) -> list[Pattern]:
    """Currently the GUI calls relay_client.list_patterns directly.
    Kept as an explicit no-op so the design-doc signature remains valid
    if we later want a caching layer here."""
    return []


def vote_on_pattern(pattern_id: str, vote: str) -> bool:
    """Currently the GUI calls relay_client.vote_pattern directly."""
    return False


# ── Local pending-candidates queue ────────────────────────────────
#
# Learner distillation (sentinel/learner.py) produces candidate patterns
# as a by-product of the hourly profile update. Instead of auto-submitting
# them (which violates the "opt-in per pattern" contract in the design
# doc above), we stash them here. The GUI shows them on top of the
# federation tab and the user approves each one explicitly.

def _candidate_hash(category: str, statement: str) -> str:
    """Stable fingerprint so two distillation runs don't enqueue the
    same candidate twice. Lowercase + stripped — small punctuation
    differences shouldn't count as distinct patterns."""
    key = f"{category}::{statement.strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def load_pending() -> dict:
    """Read the pending queue + federation stats. Shape:
       {"candidates": [{id, category, statement, confidence, sample_n,
                        created_at}, ...],
        "seen_hashes": [<hex>, ...],    # both pending and historically submitted
        "votes_cast": int,              # A2: reward counter (every Nth vote → drop)
        "patterns_shared": int,         # A2: total successful submissions
        "new_since_last_view": int}     # A2: tab badge counter
    """
    default = {
        "candidates": [],
        "seen_hashes": [],
        "votes_cast": 0,
        "patterns_shared": 0,
        "new_since_last_view": 0,
    }
    if PENDING_FILE.exists():
        try:
            data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
            # Ensure new fields are present for pre-A2 files — the
            # file predates these counters and we don't want a KeyError
            # on first launch after upgrade.
            for k, v in default.items():
                data.setdefault(k, v)
            return data
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"pending_federation.json unreadable: {e} — starting fresh")
    return default


def save_pending(data: dict) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_candidate(category: str, statement: str,
                  confidence: float = 0.6, sample_n: int = 3) -> bool:
    """Enqueue a pattern candidate from the distiller.

    Skips duplicates (same category+statement seen before, whether still
    pending or already submitted/skipped). Drops invalid shapes silently
    so one bad LLM output can't block the whole distillation cycle.

    Returns True if added, False if skipped (dup / invalid).
    """
    statement = (statement or "").strip()
    if not statement or category not in VALID_CATEGORIES:
        return False
    if len(statement) > 100:
        statement = statement[:100].rstrip()
    # Cheap client-side PII guard — the real check is server-side, but
    # filtering obvious leaks early keeps the queue clean so the user
    # doesn't see their own file paths prompted back at them.
    if _looks_like_pii(statement):
        log.info(f"Skipping candidate with PII-like content: {statement[:40]}…")
        return False

    data = load_pending()
    h = _candidate_hash(category, statement)
    if h in data.get("seen_hashes", []):
        return False

    cand = {
        "id": h,
        "category": category,
        "statement": statement,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "sample_n": max(1, int(sample_n)),
        "created_at": time.time(),
    }
    data.setdefault("candidates", []).append(cand)
    data.setdefault("seen_hashes", []).append(h)
    # Bump the "new since last view" counter so the federation tab
    # shows a badge. Reset by mark_viewed() when the user opens the tab.
    data["new_since_last_view"] = int(data.get("new_since_last_view", 0)) + 1

    # Cap queue size — drop oldest so fresh candidates can always land.
    # Cap seen_hashes separately but wider, so we keep remembering
    # "we already showed this one" even after it's been purged from
    # the active queue.
    if len(data["candidates"]) > MAX_PENDING:
        data["candidates"] = data["candidates"][-MAX_PENDING:]
    if len(data["seen_hashes"]) > MAX_PENDING * 10:
        data["seen_hashes"] = data["seen_hashes"][-MAX_PENDING * 10:]

    save_pending(data)
    log.info(f"Enqueued federation candidate: [{category}] {statement[:40]}…")
    return True


def list_pending() -> list[dict]:
    """Return current pending candidates in enqueue order (oldest first)."""
    return list(load_pending().get("candidates", []))


def _remove_candidate(candidate_id: str) -> Optional[dict]:
    data = load_pending()
    kept = []
    removed = None
    for c in data.get("candidates", []):
        if c.get("id") == candidate_id and removed is None:
            removed = c
        else:
            kept.append(c)
    data["candidates"] = kept
    save_pending(data)
    return removed


def approve_candidate(candidate_id: str) -> Optional[PatternSubmitError]:
    """Approve a pending candidate and submit it to the pool.

    On success: the candidate is removed from the pending queue.
    On refusal: the candidate is ALSO removed if the server said this
    content will never be accepted (PII/bad shape); kept otherwise so a
    transient network failure doesn't make the user re-generate.
    """
    cand = None
    for c in list_pending():
        if c.get("id") == candidate_id:
            cand = c
            break
    if cand is None:
        return PatternSubmitError(code="NOT_FOUND",
                                  message="候選已不在列表中。")

    pat = Pattern(
        category=cand["category"],
        statement=cand["statement"],
        confidence_local=cand.get("confidence", 0.6),
        sample_n=cand.get("sample_n", 3),
    )
    err = submit_pattern(pat)
    if err is None:
        _remove_candidate(candidate_id)
        return None

    # Permanent refusals: drop from queue so the user isn't stuck with
    # a zombie card that will always fail. Transient ones stay.
    if err.code in ("CONTAINS_PII", "BAD_REQUEST", "BAD_CATEGORY",
                    "BAD_STATEMENT", "TOO_FEW_SAMPLES", "BAD_CONFIDENCE"):
        _remove_candidate(candidate_id)
    return err


def skip_candidate(candidate_id: str) -> bool:
    """Remove a candidate from the queue without submitting. The hash
    stays in seen_hashes so the same candidate won't re-appear next
    distillation cycle. Returns True if removed, False if not found."""
    return _remove_candidate(candidate_id) is not None


# ── Federation stats (Phase A2: rewards + tab badge) ──────────────
#
# These counters live alongside the pending queue so there's one file
# to reason about. They're tiny (handful of ints) — no risk of the
# file growing unbounded. Vote count is tracked client-side because
# the server already knows per-user votes; we just need it locally to
# time equipment drops.


def get_stats() -> dict:
    """Return {votes_cast, patterns_shared, new_since_last_view, ...}.
    Safe to call before the file exists — returns zeros in that case."""
    data = load_pending()
    return {
        "votes_cast": int(data.get("votes_cast", 0)),
        "patterns_shared": int(data.get("patterns_shared", 0)),
        "new_since_last_view": int(data.get("new_since_last_view", 0)),
        "pending_count": len(data.get("candidates", [])),
    }


def increment_vote_counter() -> int:
    """Bump votes_cast and return the new total. Caller uses the
    return value to decide if a reward drop should fire (e.g. every
    5th vote). Does NOT decide the reward itself — that's a GUI
    concern so the equipment state lives where it belongs."""
    data = load_pending()
    data["votes_cast"] = int(data.get("votes_cast", 0)) + 1
    save_pending(data)
    return data["votes_cast"]


def increment_shared_counter() -> int:
    """Bump patterns_shared on a successful submission."""
    data = load_pending()
    data["patterns_shared"] = int(data.get("patterns_shared", 0)) + 1
    save_pending(data)
    return data["patterns_shared"]


def mark_viewed() -> None:
    """Reset the 'new since last view' counter. Called when the user
    opens the federation tab so the badge clears on acknowledgment."""
    data = load_pending()
    if data.get("new_since_last_view", 0):
        data["new_since_last_view"] = 0
        save_pending(data)


# ── Local helpers that ARE usable today ───────────────────────────

def validate_pattern(pattern: Pattern) -> Optional[PatternSubmitError]:
    """Client-side validation before submission. Server will check
    again, but failing fast here saves round-trips."""
    if pattern.category not in VALID_CATEGORIES:
        return PatternSubmitError(
            code="BAD_CATEGORY",
            message=f"category must be one of {sorted(VALID_CATEGORIES)}",
        )
    if not pattern.statement or len(pattern.statement) > 100:
        return PatternSubmitError(
            code="BAD_STATEMENT",
            message="statement must be 1-100 chars",
        )
    if _looks_like_pii(pattern.statement):
        return PatternSubmitError(
            code="CONTAINS_PII",
            message=(
                "statement 看起來含有個資（路徑／email／URL 等），"
                "請改寫成純粹的行為描述"
            ),
        )
    if not (0.0 <= pattern.confidence_local <= 1.0):
        return PatternSubmitError(
            code="BAD_CONFIDENCE",
            message="confidence_local must be in [0, 1]",
        )
    if pattern.sample_n < 3:
        return PatternSubmitError(
            code="TOO_FEW_SAMPLES",
            message="pattern needs at least 3 observations before sharing",
        )
    return None


def _looks_like_pii(text: str) -> bool:
    """Conservative PII check. False positives are better than false
    negatives here — if we reject a legitimate pattern, the user gets
    a clear error and rewords it. If we accept a leaked one, bad."""
    import re
    patterns = [
        r"[\w.+-]+@[\w-]+\.[\w.-]+",          # email
        r"https?://\S+",                       # URL
        r"[A-Za-z]:[\\/]",                     # Windows path
        r"/[a-z][a-z0-9_-]*/[a-z0-9_-]+",      # Unix-ish path
        r"\b\d{9,}\b",                         # long digit run (phone/id)
        r"\b[0-9a-f]{7,}\b",                   # hex tokens (hashes, IDs)
    ]
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False
