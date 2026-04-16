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

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("sentinel.growth.federation")

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


# ── Stubs — PR 5 will implement these ─────────────────────────────

def submit_pattern(pattern: Pattern) -> Optional[PatternSubmitError]:
    """Submit a pattern to the community pool.

    Currently: logs and returns an error. The relay doesn't have these
    endpoints yet. See PR 5.
    """
    log.warning(
        "federation.submit_pattern called but not yet implemented "
        "(pattern category=%s). See PR 5.",
        pattern.category,
    )
    return PatternSubmitError(
        code="NOT_IMPLEMENTED",
        message=(
            "聯邦學習協議尚未實裝（PR 5 才會完成）。你的觀察已儲存在本地。"
        ),
    )


def fetch_community_patterns(category: Optional[str] = None,
                             limit: int = 20) -> list[Pattern]:
    """Fetch community-approved patterns. Currently returns empty."""
    log.warning(
        "federation.fetch_community_patterns called but not yet implemented. "
        "See PR 5."
    )
    return []


def vote_on_pattern(pattern_id: str, vote: str) -> bool:
    """Vote confirm/refute/unclear on a pattern. vote ∈ {'confirm',
    'refute', 'unclear'}. Returns False (not implemented)."""
    log.warning(
        "federation.vote_on_pattern called but not yet implemented. "
        "See PR 5."
    )
    return False


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
