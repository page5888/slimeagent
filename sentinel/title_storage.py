"""Title storage — the 箱子 metadata layer.

Per ADR `docs/decisions/2026-04-30-title-system.md`. This file owns
**only** the schema + persistence. Title generation (LLM prompt,
morality vet, candidate scoring) lives in the future
`sentinel/title_system.py`. Natural invocation in chat lives in the
future `sentinel/title_invoker.py`. This module is the foundation
both will read and write.

What's stored
-------------

Every title proposal that ever fires — accepted, rejected, renamed,
or pending. Rejected titles still need to live in storage because
ADR 紅線 #4 says Slime learns the rejection *style*, which means
later code paths need to read past rejections to avoid repeating
the pattern. ADR 紅線 #8 ("冷凍而非死亡") similarly forbids
deletion: cold-storage via `frozen_until` is the only legal
exit path.

Schema invariants (from ADR 紅線 1-3)
------------------------------------

- `day_marker` is required (紅線 #2 — 沒 day_marker 的稱號是非法的)
- `events_referenced` should be non-empty for `master_response ==
  "accepted"` titles (紅線 #1 — 必須對應實際事件). Storage doesn't
  enforce this — generation layer's job. We expose `is_well_formed`
  for callers that want to check.
- `master_response` defaults to "pending"; only the master-decision
  path (UI / chat) can flip it (紅線 #10 — 不單方面定義自己).

What this module does NOT do
----------------------------

- Generate titles (no LLM, no prompt, no morality vet)
- Match context tags to chat input
- Render anything in the GUI
- Trigger emergent / master-summoned creation

Those each get their own PR per the ADR's roadmap.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.title_storage")


# ─── Constants — string enums ────────────────────────────────────────────
#
# String constants instead of Enum so JSON serialization is direct and
# old saves stay readable through schema evolution. Adding a new value
# is a one-liner; an Enum migration is not.

class Trigger:
    EMERGENT = "emergent"           # impulse engine fired
    MASTER_SUMMONED = "master_summoned"   # master asked
    NAMING_CEREMONY = "naming_ceremony"   # D14/D30 naming produces first title

    ALL = (EMERGENT, MASTER_SUMMONED, NAMING_CEREMONY)


class MasterResponse:
    PENDING = "pending"             # waiting for master decision
    ACCEPTED = "accepted"           # title goes into 箱子
    REJECTED = "rejected"           # ADR 紅線 #4 — Slime 學風格不學內容
    RENAMED = "renamed"             # master proposed alternative; see master_renamed_to

    ALL = (PENDING, ACCEPTED, REJECTED, RENAMED)
    # The 'in 箱子' set — a title appears in the user-visible 箱子 if its
    # response is accepted or renamed (renamed titles enter under the
    # master's chosen name).
    IN_BOX = (ACCEPTED, RENAMED)


class InvocationResponse:
    """How the master responded the last time slime referenced this title
    in chat. ADR 紅線 #5 (frequency cap) + 紅線 #9 (silence is not
    negative) drive when we 'freeze' a title. Cold storage logic isn't
    here — it's title_invoker's job — but the storage records the
    history so the invoker can read it."""
    POSITIVE = "positive"           # master engaged with the reference
    NEUTRAL = "neutral"             # master neither rejected nor amplified
    SILENT = "silent"               # not a negative — see ADR 紅線 #9
    NEGATIVE = "negative"           # master pushed back


# ─── Schema ──────────────────────────────────────────────────────────────


@dataclass
class EventReference:
    """A specific event in slime's memory that this title is named for.

    Required to be non-empty for accepted titles per ADR 紅線 #1
    ('稱號必須對應實際發生的事件 — 不能憑空生成'). Enforcement happens
    in the generation layer, not storage; we trust the caller."""
    day: int                        # day-of-life when event happened
    summary: str                    # short prose, master-readable


@dataclass
class InvocationRecord:
    """One past attempt to use this title in chat."""
    date: float                     # epoch seconds
    master_responded: str           # InvocationResponse.*


@dataclass
class Title:
    """A single 稱號. Proposed → accepted/rejected/renamed → maybe
    invoked over time → maybe frozen.

    Schema mirrors ADR § 完整資料 Schema (line 138-168). Field order
    matches that section so future readers can cross-reference 1:1."""

    # Identity / display
    id: str                         # uuid4 hex; stable across saves
    title: str                      # the 稱號 itself, e.g. 「陪過低潮的史萊姆」
    day_marker: int                 # ADR 紅線 #2: required, never optional

    # Provenance
    created_at: float               # epoch seconds
    trigger: str                    # Trigger.*
    events_referenced: list[EventReference] = field(default_factory=list)

    # Master decision
    master_response: str = MasterResponse.PENDING
    master_renamed_to: Optional[str] = None  # only set when RENAMED

    # Invocation rules
    context_tags: list[str] = field(default_factory=list)
    do_not_invoke_when: list[str] = field(default_factory=list)
    invocation_history: list[InvocationRecord] = field(default_factory=list)

    # Cold storage. None = active; epoch float = frozen until that time.
    # ADR 紅線 #8: 冷凍而非死亡. Once frozen the title still lives in
    # storage; only invocation is suppressed.
    frozen_until: Optional[float] = None

    def display_text(self) -> str:
        """Format for display in the box: title + day marker.

        ADR § Q7: 「陪過低潮的史萊姆 (D198)」. Renamed titles use the
        master's chosen name; the original is not surfaced to the user."""
        name = self.master_renamed_to or self.title
        return f"{name} (D{self.day_marker})"

    def is_in_box(self) -> bool:
        """Whether this title appears in the user-visible 箱子."""
        return self.master_response in MasterResponse.IN_BOX

    def is_frozen(self, now: Optional[float] = None) -> bool:
        """Is the title currently in cold storage?"""
        if self.frozen_until is None:
            return False
        return (now or time.time()) < self.frozen_until

    def is_well_formed(self) -> bool:
        """Lightweight invariant check — what storage *can* enforce
        without depending on title_system. Generation layer should
        run a stricter validator before persisting."""
        if not self.title.strip():
            return False
        if self.day_marker < 0:
            return False
        if self.trigger not in Trigger.ALL:
            return False
        if self.master_response not in MasterResponse.ALL:
            return False
        # ADR 紅線 #1: accepted titles must reference at least one event
        if (self.master_response == MasterResponse.ACCEPTED
                and not self.events_referenced):
            return False
        # Renamed titles must carry the new name
        if self.master_response == MasterResponse.RENAMED \
                and not (self.master_renamed_to or "").strip():
            return False
        return True


# ─── Persistence ─────────────────────────────────────────────────────────

TITLES_FILE = Path.home() / ".hermes" / "aislime_titles.json"


def new_title_id() -> str:
    """Stable id for a new title. Hex form so it survives the JSON
    round-trip with no quoting weirdness."""
    return uuid.uuid4().hex


def _title_from_dict(d: dict) -> Title:
    """Reconstruct a Title (and nested dataclasses) from raw JSON.

    Tolerant of older saves missing newer fields — defaults from the
    dataclass apply. Unknown fields are silently dropped (not raised),
    which lets us forward-compat: an older binary loading a newer save
    drops new fields but doesn't crash."""
    valid = {f.name for f in Title.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in d.items() if k in valid}

    kwargs["events_referenced"] = [
        EventReference(**e) for e in d.get("events_referenced", [])
    ]
    kwargs["invocation_history"] = [
        InvocationRecord(**r) for r in d.get("invocation_history", [])
    ]
    # context_tags / do_not_invoke_when are flat lists of strings —
    # default_factory handles missing.
    return Title(**kwargs)


def load_titles() -> list[Title]:
    """Read all titles from disk. Empty list if no file yet.

    Corrupt-file handling mirrors evolution.load_evolution: do NOT
    silently overwrite a damaged save (that bug wiped Mac users
    back to newborn). Back it up and start fresh; surface loudly.
    Same justification: the box is irreplaceable if lost."""
    if not TITLES_FILE.exists():
        return []
    try:
        raw = json.loads(TITLES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        backup = TITLES_FILE.with_suffix(f".broken.{int(time.time())}.json")
        try:
            TITLES_FILE.rename(backup)
            log.error(
                "Failed to load titles: %r. Backed up corrupt save to %s. "
                "Starting fresh — recover from the backup if needed.",
                e, backup,
            )
        except Exception as backup_err:
            log.error(
                "Failed to load titles: %r. Also failed to back up: %r. "
                "Leaving %s in place; do not restart until fixed.",
                e, backup_err, TITLES_FILE,
            )
            raise
        return []

    if not isinstance(raw, list):
        log.error("titles file did not contain a list (got %s); "
                  "ignoring contents.", type(raw).__name__)
        return []

    titles = []
    for d in raw:
        try:
            titles.append(_title_from_dict(d))
        except Exception as e:
            # One broken row should not torpedo the whole box.
            log.warning("skipping malformed title row: %r (%s)", d, e)
    return titles


def save_titles(titles: list[Title]) -> None:
    """Write all titles to disk, atomically.

    Atomic write via tmp-then-rename: a half-written file would lose
    the box, so we never partial-write the real path. Same pattern
    Python's stdlib uses for json updates."""
    TITLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TITLES_FILE.with_suffix(".tmp")
    payload = [asdict(t) for t in titles]
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(TITLES_FILE)


# ─── High-level helpers ──────────────────────────────────────────────────


def add_title(title: Title) -> None:
    """Append a new title to storage. Does not validate — caller's job.
    Refuses to add if id collides with an existing title (defensive
    against generator regression)."""
    existing = load_titles()
    if any(t.id == title.id for t in existing):
        raise ValueError(f"title id collision: {title.id}")
    existing.append(title)
    save_titles(existing)


def find_title(title_id: str) -> Optional[Title]:
    for t in load_titles():
        if t.id == title_id:
            return t
    return None


def update_title(updated: Title) -> bool:
    """Replace a title in storage by id. Returns True if found,
    False if no row matched (caller decides whether to add)."""
    titles = load_titles()
    for i, t in enumerate(titles):
        if t.id == updated.id:
            titles[i] = updated
            save_titles(titles)
            return True
    return False


def accepted_titles() -> list[Title]:
    """Filter to titles the master accepted (or renamed) — the
    user-visible 箱子 view. Frozen titles still appear: they're
    in cold storage from the chat-invocation perspective only,
    not removed from history."""
    return [t for t in load_titles() if t.is_in_box()]
