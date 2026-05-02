"""LLM fallback for window-title semantic understanding.

Phase 3b of v0.8 sensor refactor (2026-05-02). Sits on top of the
pure rule layer (`window_semantics.interpret_window`) and handles
the long tail — titles where rules say `confidence == "unknown"`.

Why this is a separate module
-----------------------------

`window_semantics.py` is intentionally pure (no LLM, no IO, no
globals). Phase 3a tests pin that purity. This module wraps it with
an LLM fallback + a persistent cache; importers that want the
free-of-charge rule path can keep using `interpret_window` directly,
and Phase 5's impulse engine can choose per-call whether to spend
LLM cost on the long tail.

Caching
-------

LLM calls cost tokens; the daemon polls every 2s; un-cached re-
interpretation of the same Reddit thread on every poll would burn
through quota fast. So:

- Cache key: `(process_name, window_title)` joined by a control char
  separator so neither field's contents collide.
- Value: the full semantic dict + meta (`interpreted_at` epoch,
  `model` label). Meta lets a future eviction policy use age + LRU.
- Persistence: `~/.hermes/aislime_window_semantics_cache.json`
  written atomically (`.tmp` → `.replace()`) on each new entry.
- Size cap: `MAX_CACHE_ENTRIES`. When exceeded we evict the oldest
  by `interpreted_at`. This is FIFO-ish, not LRU, because the
  daemon poll pattern means the same active window gets read on
  every tick — true LRU would never evict the master's recent
  windows even if they're stable. FIFO with age-bound cap matches
  the actual usage shape.

LLM cost-control choices
-----------------------

- Temperature 0.2: same input should mostly produce the same output
  so the cache works as expected.
- Max tokens 250: a JSON dict with our schema fits in well under
  150; 250 leaves slack for a verbose model without truncating.
- Hard JSON parse: on parse failure we don't retry — return None,
  caller falls back to the rule layer's "unknown" answer. Better to
  show "unknown" than spin LLM calls trying to fix bad output.
- No streaming: single call, single parse.

Cache→rule promotion
-------------------

施工指示 mentioned "定期把 LLM 判斷 cache 起來變成新規則". This PR
deliberately does NOT auto-promote — LLM judgments can vary across
runs (even at temperature 0.2), and an auto-promoter would bake
inconsistencies into the rule layer where they're hard to remove.
The cache file is there for periodic human review: a future PR can
read it, see which titles repeatedly fall through, and add proper
rules to `window_semantics.py` by hand.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from sentinel.window_semantics import (
    AppCategory, ContentType, Confidence,
    interpret_window,
)

log = logging.getLogger("sentinel.window_semantics_llm")

# Path lives alongside other ~/.hermes/ slime data. Nothing else
# should write here; eviction is internal to this module.
CACHE_FILE = Path.home() / ".hermes" / "aislime_window_semantics_cache.json"

# Tunables. Exposed at module level so tests can override without
# patching internals.
MAX_CACHE_ENTRIES = 5000
LLM_TEMPERATURE = 0.2
LLM_MAX_TOKENS = 250

# Cache key separator — a control char that's vanishingly unlikely
# to appear in real process names / window titles.
_KEY_SEP = ""


# ─── Cache load / save ───────────────────────────────────────────────────
#
# Module-level singleton. Loaded lazily on first access; the daemon
# loop calls into us on every tick so we want one load, not N.

_cache: Optional[dict[str, dict]] = None


def _make_key(process_name: str, window_title: str) -> str:
    return f"{process_name or ''}{_KEY_SEP}{window_title or ''}"


def _load_cache() -> dict[str, dict]:
    """Read the cache from disk into a fresh dict. Failure modes
    (file missing, JSON corrupt) all degrade to empty dict — same
    decision as title_storage.load_titles, with the same justification:
    a missing/broken cache should never block rendering, only force a
    re-LLM-call at worst."""
    if not CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
        log.warning("cache file did not contain a dict; ignoring")
    except Exception as e:
        log.warning("cache load failed: %r; starting fresh", e)
    return {}


def _save_cache(cache: dict[str, dict]) -> None:
    """Atomic write — same .tmp → replace() pattern as title_storage.
    Half-written cache files would force a fresh re-population on the
    next launch; the atomic write avoids that."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(cache, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(CACHE_FILE)
    except OSError as e:
        # Disk full / readonly home / etc — log once, don't crash the
        # daemon. The in-memory cache continues working until next
        # restart; only the persistence guarantee is lost.
        log.warning("cache save failed: %r", e)


def _get_cache() -> dict[str, dict]:
    global _cache
    if _cache is None:
        _cache = _load_cache()
    return _cache


def _evict_if_oversized(cache: dict[str, dict]) -> None:
    """Drop oldest entries by `interpreted_at` until cache is under
    the size cap. FIFO-ish, see module docstring for why not LRU."""
    if len(cache) <= MAX_CACHE_ENTRIES:
        return
    # Sort by interpreted_at ascending (oldest first)
    by_age = sorted(
        cache.items(),
        key=lambda kv: kv[1].get("interpreted_at", 0),
    )
    excess = len(cache) - MAX_CACHE_ENTRIES
    for key, _ in by_age[:excess]:
        cache.pop(key, None)


def reset_cache_for_tests() -> None:
    """Test-only: drop the in-memory cache so the next call re-loads
    from disk (or finds the patched test path empty)."""
    global _cache
    _cache = None


# ─── LLM prompt + parse ─────────────────────────────────────────────────


_PROMPT_TEMPLATE = """You translate one Windows window title into a small JSON object describing what the master is doing.

Process: {process}
Title:   {title}

Output ONLY this JSON, no prose, no markdown fences:
{{
  "app_category":  "<one of: browser, ide, messaging, video, audio, document, terminal, file_browser, game, unknown>",
  "content_type":  "<one of: coding, social_discussion, video_watching, music_listening, reading, conversation, shell, browsing, file_navigation, gaming, unknown>",
  "topic_signal":  "<short human-readable hint, max 60 chars, what is the master engaging with>",
  "platform":      "<browser-only: reddit/youtube/github/etc; empty string if not in a browser or unknown>",
  "file":          "<IDE-only: filename being edited; empty otherwise>",
  "project":       "<IDE-only: project name; empty otherwise>",
  "contact":       "<messaging-only: contact or channel name; NEVER include any message content; empty otherwise>"
}}

Privacy rules (hard):
- For messaging apps, "contact" gets the CHANNEL or PERSON name only. Never any message preview, content, or topic.
- If the title contains a notification preview, return contact = the sender's name only, ignore the preview.
- Don't speculate about the master's mood, intent, or private state.

If you genuinely cannot tell what category, use "unknown" for both app_category and content_type, and put the title (truncated) into topic_signal.
"""


def _build_prompt(process_name: str, window_title: str) -> str:
    # Truncate inputs to keep prompt size bounded; window titles can
    # get long with full URLs in them.
    proc = (process_name or "")[:120]
    title = (window_title or "")[:300]
    return _PROMPT_TEMPLATE.format(process=proc, title=title)


_SCHEMA_KEYS = (
    "app_category", "content_type", "topic_signal",
    "platform", "file", "project", "contact",
)


def _parse_llm_json(raw: str) -> Optional[dict]:
    """Extract the JSON object from an LLM reply. The prompt asks for
    raw JSON, but defensive parsing handles common wrappers (markdown
    fences, trailing whitespace, leading prose)."""
    if not raw:
        return None
    s = raw.strip()
    # Strip fences if the model added them despite instructions
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s)
    # Find the outermost JSON object span
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        obj = json.loads(s[start: end + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    # Validate the schema — every key must be present, must be string.
    # We *don't* validate the value enums beyond category/content
    # belonging to known sets; an LLM hallucinating a new platform
    # name like "kakaotalk_web" is fine — Phase 5 just sees it as a
    # string.
    out: dict = {}
    for key in _SCHEMA_KEYS:
        v = obj.get(key, "")
        if not isinstance(v, str):
            v = ""
        out[key] = v.strip()

    # Lock down category / content to known values
    if out["app_category"] not in AppCategory.ALL:
        out["app_category"] = AppCategory.UNKNOWN
    # ContentType doesn't expose .ALL; use the same canonical list as
    # the prompt enumerates so we stay aligned.
    valid_content = {
        "coding", "social_discussion", "video_watching",
        "music_listening", "reading", "conversation",
        "shell", "browsing", "file_navigation", "gaming", "unknown",
    }
    if out["content_type"] not in valid_content:
        out["content_type"] = ContentType.UNKNOWN

    # topic_signal length cap (same as rule layer's _truncate_for_signal)
    if len(out["topic_signal"]) > 80:
        out["topic_signal"] = out["topic_signal"][:79] + "…"

    return out


def _call_llm_for_window(snapshot: dict) -> Optional[dict]:
    """One LLM call for a single snapshot. Returns the parsed dict or
    None on any failure path (LLM unreachable, bad JSON, schema
    invalid). Caller's job to decide what to do with None."""
    from sentinel.llm import call_llm

    process = (snapshot.get("process_name") or "").strip()
    title   = (snapshot.get("window_title") or "").strip()
    if not process and not title:
        return None

    prompt = _build_prompt(process, title)
    raw = call_llm(
        prompt,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
        task_type="chat",
    )
    if not raw:
        return None
    return _parse_llm_json(raw)


# ─── Public entry point ─────────────────────────────────────────────────


def interpret_window_with_llm(
    snapshot: dict, *, use_llm: bool = True,
) -> dict:
    """Hybrid rule + LLM interpretation.

    Returns the same schema as `window_semantics.interpret_window`.

    Decision tree:
      1. Run rule layer.
      2. If rule confidence != UNKNOWN → return rule result.
         (High-confidence rule answers always win — they're cheap,
         deterministic, and the LLM can't do better on what the
         rule already nails.)
      3. If `use_llm=False` → return rule result (cost gate for
         tests / dry runs / power-saver mode).
      4. Cache hit → return cached result.
      5. LLM call → on success, cache + return.
         On failure → return rule result (the "unknown" answer).
    """
    rule_result = interpret_window(snapshot)
    if rule_result.get("confidence") != Confidence.UNKNOWN:
        return rule_result
    if not use_llm:
        return rule_result

    process = (snapshot.get("process_name") or "").strip()
    title   = (snapshot.get("window_title") or "").strip()
    if not process and not title:
        return rule_result

    cache = _get_cache()
    key = _make_key(process, title)
    cached = cache.get(key)
    if cached is not None:
        # Reconstruct the full semantic dict shape the rule layer
        # uses — caller doesn't care that this came from cache, just
        # that it has every key.
        return _materialize_semantic_dict(cached, snapshot)

    llm_result = _call_llm_for_window(snapshot)
    if llm_result is None:
        # Don't cache failures — the LLM might be transiently down;
        # next call should retry rather than serve "unknown" forever.
        return rule_result

    # Cache the full semantic dict + meta. We store fields that
    # _parse_llm_json populates; merging with rule_result on read
    # restores any keys the LLM didn't have to fill (is_idle, etc).
    entry = {
        **llm_result,
        "interpreted_at": time.time(),
        "model": _which_model(),
    }
    cache[key] = entry
    _evict_if_oversized(cache)
    _save_cache(cache)

    return _materialize_semantic_dict(entry, snapshot)


def _materialize_semantic_dict(entry: dict, snapshot: dict) -> dict:
    """Combine a cache entry (LLM-derived semantic fields) with
    snapshot-derived fields like is_idle. Output matches the rule
    layer's schema 1:1 so downstream consumers can't tell whether
    the answer came from rules or LLM."""
    out: dict = {
        "app_category": entry.get("app_category", AppCategory.UNKNOWN),
        "content_type": entry.get("content_type", ContentType.UNKNOWN),
        "topic_signal": entry.get("topic_signal", ""),
        "platform":     entry.get("platform", ""),
        "file":         entry.get("file", ""),
        "project":      entry.get("project", ""),
        "contact":      entry.get("contact", ""),
        # LLM is treated as MEDIUM confidence — better than unknown
        # but explicit that this isn't the deterministic rule path.
        "confidence":   Confidence.MEDIUM,
        "is_idle":      bool(snapshot.get("is_idle", False)),
    }
    return out


def _which_model() -> str:
    """Best-effort label for which model produced this cache entry.
    Used for periodic human review of cache contents — if entries
    look wrong, you want to know which model wrote them. Falls back
    to 'unknown' if config isn't available."""
    try:
        from sentinel import config
        return getattr(config, "PRIMARY_MODEL", "unknown") or "unknown"
    except Exception:
        return "unknown"
