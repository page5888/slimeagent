"""Context Bus — source-keyed publish/subscribe for LLM context assembly.

Why this exists
---------------
Before Phase B1, every place that called an LLM had to manually pass
snapshots, file events, Claude activity, user activity, … to a hand-
rolled builder (brain.build_context). Adding a new observation source
meant threading another argument through every caller and extending
the builder's `if` chain. That didn't scale for the Phase C/D plan
(vector memory, screen VLM results, federation confirmed patterns —
all would need to land in the LLM prompt).

The bus inverts the flow: observation sources **publish** to a keyed
bucket whenever they have news; LLM callers **render** the current
state of all buckets into a prompt on demand. Adding a new source is
a one-liner; no consumer has to learn about it.

Inspired by moeru-ai/airi's `packages/stage-ui/src/stores/chat/
context-store.ts` (ReplaceSelf strategy, source keys), simplified for
our single-process desktop app.

Design
------
- Single shared bus per process (module-level `_bus` + `get_bus()`).
- Thread-safe (observation sources run on daemon threads).
- Two update strategies per source:
    REPLACE_SELF — new publish replaces the existing entry (system
                   snapshots, most recent screen interpretation, etc.)
    APPEND       — keeps up to `max_items` newest entries (file events,
                   input chunks, user activity streams).
- Rendering produces a deterministic ordered text block, respecting
  per-source priority so the most important signals appear first in
  the LLM prompt.
- Stale-entry TTL is opt-in via `ttl_seconds` on registration — a
  source that's been silent too long stops contributing without
  needing explicit clears.

Non-goals for B1
----------------
- No persistence. The bus is pure in-memory state; the on-disk
  learning log (aislime_learning_log.jsonl) remains the durable
  record.
- No cross-process sync. Single desktop app, single process.
- No structured API for consumers (yet). Render returns prompt text;
  Phase B2 (vector memory) will add a structured `get_entries()` so
  memory retrievers can score relevance.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Strategy(Enum):
    """How new content from a source interacts with existing content."""
    REPLACE_SELF = "replace_self"   # Newest entry wins; older discarded.
    APPEND = "append"               # Keep last N entries as a rolling window.


@dataclass
class SourceSpec:
    """Registration metadata for a context source.

    `label` is what appears in the rendered prompt as a section header
    (e.g. "系統狀態"). `priority` is sort order — lower numbers render
    first. `ttl_seconds` is optional; if set, entries older than ttl
    are ignored at render time without being explicitly cleared.
    """
    key: str
    label: str
    priority: int = 100
    strategy: Strategy = Strategy.REPLACE_SELF
    max_items: int = 10           # Only used for APPEND.
    ttl_seconds: Optional[float] = None


@dataclass
class Entry:
    content: str
    timestamp: float = field(default_factory=time.time)


class ContextBus:
    """Thread-safe keyed context store.

    Observation sources call `publish(source_key, content)`. LLM
    callers call `render()` to get a prompt-ready string.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sources: dict[str, SourceSpec] = {}
        self._entries: dict[str, list[Entry]] = {}

    # ── Registration ───────────────────────────────────────────────

    def register(self, spec: SourceSpec) -> None:
        """Declare a new context source. Safe to call repeatedly with
        the same spec (idempotent); later calls with a different spec
        replace the registration — handy when a module imports itself
        during development."""
        with self._lock:
            self._sources[spec.key] = spec
            self._entries.setdefault(spec.key, [])

    # ── Publish ────────────────────────────────────────────────────

    def publish(self, source_key: str, content: str) -> None:
        """Post content to a source's bucket.

        If the source isn't registered yet, we auto-register with
        defaults. That way ad-hoc `bus.publish("debug", ...)` still
        works during exploration; structured callers should register
        explicitly for priority/strategy control.
        """
        if not content:
            return
        with self._lock:
            spec = self._sources.get(source_key)
            if spec is None:
                spec = SourceSpec(key=source_key, label=source_key)
                self.register(spec)
            bucket = self._entries.setdefault(source_key, [])
            entry = Entry(content=content)
            if spec.strategy is Strategy.REPLACE_SELF:
                bucket.clear()
                bucket.append(entry)
            else:  # APPEND
                bucket.append(entry)
                if len(bucket) > spec.max_items:
                    # Drop oldest so newest N survive.
                    del bucket[0 : len(bucket) - spec.max_items]

    # ── Query ──────────────────────────────────────────────────────

    def get_entries(self, source_key: str) -> list[Entry]:
        """Snapshot copy of a source's current entries (TTL-filtered)."""
        with self._lock:
            spec = self._sources.get(source_key)
            entries = list(self._entries.get(source_key, []))
        if spec and spec.ttl_seconds is not None:
            cutoff = time.time() - spec.ttl_seconds
            entries = [e for e in entries if e.timestamp >= cutoff]
        return entries

    def clear(self, source_key: Optional[str] = None) -> None:
        """Drop entries from one source (or all if key omitted)."""
        with self._lock:
            if source_key is None:
                for k in self._entries:
                    self._entries[k] = []
            elif source_key in self._entries:
                self._entries[source_key] = []

    # ── Render ─────────────────────────────────────────────────────

    def render(self, include_empty: bool = False) -> str:
        """Assemble all sources into a single prompt string.

        Sections appear in priority order (low → high). Empty sources
        are skipped unless `include_empty=True` (useful for debugging
        "why is the slime not seeing X").
        """
        with self._lock:
            specs = sorted(self._sources.values(), key=lambda s: (s.priority, s.key))
            now = time.time()
            sections: list[str] = []
            for spec in specs:
                entries = self._entries.get(spec.key, [])
                if spec.ttl_seconds is not None:
                    entries = [e for e in entries if now - e.timestamp <= spec.ttl_seconds]
                if not entries and not include_empty:
                    continue
                sections.append(f"=== {spec.label} ===")
                if entries:
                    for e in entries:
                        sections.append(e.content)
                else:
                    sections.append("(尚無資料)")
                sections.append("")  # blank line between sections
        # Strip trailing blank line for cleanliness
        return "\n".join(sections).rstrip()


# ── Module-level default instance ─────────────────────────────────
#
# A single shared bus per process keeps things simple — every module
# that publishes and every LLM caller that renders operates on the
# same state without having to plumb references through constructors.
# If we ever need per-conversation or per-user isolation we can add
# a factory, but for a single-user desktop app this is overkill.

_bus: ContextBus = ContextBus()


def get_bus() -> ContextBus:
    """Fetch the shared bus instance. Use this everywhere that wants
    to publish or render context."""
    return _bus


# ── Canonical source registrations ────────────────────────────────
#
# Registering them at import time (rather than requiring each module
# to register on first use) gives consistent priority/label across
# the codebase and makes the full list of sources greppable from
# exactly one place.
#
# Priority: lower number renders earlier. System state goes first
# because the LLM benefits from knowing "the machine is on fire"
# before processing file-level events. Federation/memory inputs
# (added in Phase B2+) will slot in with higher numbers so they
# appear after live signals.

SOURCE_SYSTEM = SourceSpec(
    key="system",
    label="系統狀態",
    priority=10,
    strategy=Strategy.REPLACE_SELF,
    ttl_seconds=300,   # 5 min — a stale snapshot is worse than none
)
SOURCE_FILES = SourceSpec(
    key="files",
    label="檔案變動",
    priority=20,
    strategy=Strategy.REPLACE_SELF,
    ttl_seconds=600,
)
SOURCE_CLAUDE = SourceSpec(
    key="claude",
    label="Claude Code 活動",
    priority=30,
    strategy=Strategy.REPLACE_SELF,
    ttl_seconds=600,
)
SOURCE_ACTIVITY = SourceSpec(
    key="activity",
    label="使用者活動",
    priority=40,
    strategy=Strategy.REPLACE_SELF,
    ttl_seconds=600,
)
SOURCE_INPUT = SourceSpec(
    key="input",
    label="輸入節奏",
    priority=50,
    strategy=Strategy.REPLACE_SELF,
    ttl_seconds=600,
)
SOURCE_SCREEN = SourceSpec(
    key="screen",
    label="螢幕觀察",
    priority=60,
    strategy=Strategy.REPLACE_SELF,
    ttl_seconds=600,
)
SOURCE_MEMORY = SourceSpec(
    key="memory",
    label="相關記憶",
    priority=90,
    strategy=Strategy.APPEND,
    max_items=5,
    ttl_seconds=None,     # memory doesn't expire mid-conversation
)

for _spec in (
    SOURCE_SYSTEM, SOURCE_FILES, SOURCE_CLAUDE, SOURCE_ACTIVITY,
    SOURCE_INPUT, SOURCE_SCREEN, SOURCE_MEMORY,
):
    _bus.register(_spec)
