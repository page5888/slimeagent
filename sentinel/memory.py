"""Long-term semantic memory — SQLite + sqlite-vec.

Why this exists
---------------
Before Phase B2, the slime's "memory" was two JSON files:
  - sentinel_memory.json holding a free-text profile the LLM rewrote
    every distillation cycle (so nothing was preserved verbatim —
    only the latest summary)
  - aislime_learning_log.jsonl, append-only log nobody read

Neither supported the question that drives real companionship: "has
master mentioned this before?" The profile was a lossy compression;
the log was write-only forensics. You couldn't recall a specific
past exchange, a federation pattern master had validated, or an
observation the slime made two weeks ago.

This module gives the slime actual long-term memory: every chat turn,
distilled observation, and confirmed federation pattern is embedded
and stored in a local sqlite-vec index. Before any LLM call, the
caller can retrieve semantically-relevant memories and feed them
into the prompt via the Context Bus's SOURCE_MEMORY bucket.

Design
------
- **Storage**: SQLite at ~/.hermes/aislime_memory.db
  - `memories` table: text + kind + metadata + hash + timestamps
  - `memory_vectors` virtual table (vec0): id → embedding
- **Embeddings**: Gemini text-embedding-004 (768-dim). Uses the same
  API key already configured for chat/analysis, so no extra setup.
  If embedding fails (rate limit, network, missing key), the memory
  is stored without a vector — still retrievable by kind/text search
  but not by semantic similarity. Never blocks the caller.
- **Dedup**: sha1 of normalized text. Repeated distillations over the
  same profile snapshot won't multiply.
- **Retrieval**: cosine similarity top-k via sqlite-vec. Optional
  filter by kind. Updates retrieval_count + last_seen_at so future
  work can prefer "useful" memories.

Non-goals
---------
- Cross-device sync. Local DB only.
- Memory editing UI (yet). Exposed via recall/forget Python API only.
- Multi-modal memory. Text only for B2; screen-capture VLM results
  will land in a future phase alongside the vision pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from sentinel import config

log = logging.getLogger("sentinel.memory")

MEMORY_DB = Path.home() / ".hermes" / "aislime_memory.db"
EMBED_DIM = 768           # Gemini text-embedding-004
EMBED_MODEL = "text-embedding-004"

# Known memory kinds. Enforced softly — unrecognized kinds are stored
# too, since the module is meant to grow with the project, but the
# known-set lets callers filter explicitly without risking typos.
KIND_CHAT = "chat"
KIND_DISTILL_PROFILE = "distill_profile"
KIND_DISTILL_OBSERVATION = "distill_observation"
KIND_FEDERATION = "federation_pattern"
KIND_USER_NOTE = "user_note"


# ── DB bootstrap ───────────────────────────────────────────────────


def _load_vec_extension(conn: sqlite3.Connection) -> bool:
    """Attempt to load sqlite-vec into the given connection.

    Returns True on success, False if the extension isn't available
    (in which case the module degrades gracefully — memories still get
    stored, but semantic recall is disabled).
    """
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as e:
        log.warning(f"sqlite-vec extension unavailable ({e}); "
                    f"semantic recall disabled. "
                    f"pip install sqlite-vec to enable.")
        return False


def _connect() -> tuple[sqlite3.Connection, bool]:
    """Open the memory DB, run migrations, return (conn, has_vec)."""
    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MEMORY_DB))
    conn.row_factory = sqlite3.Row
    has_vec = _load_vec_extension(conn)

    # Base schema — always available even if vec extension missing.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            text            TEXT    NOT NULL,
            kind            TEXT    NOT NULL,
            metadata_json   TEXT,
            content_hash    TEXT    NOT NULL,
            created_at      REAL    NOT NULL,
            last_seen_at    REAL,
            retrieval_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_hash ON memories(content_hash);
        CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
        CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
        """
    )

    if has_vec:
        # vec0 table co-indexed by memories.id. distance_metric=cosine
        # because Gemini's text-embedding-004 output is L2-normalized,
        # so cosine distance is the semantically meaningful choice
        # (default L2 would work but "similarity = 1 - distance"
        # doesn't read cleanly when distance isn't bounded).
        try:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors "
                f"USING vec0(id INTEGER PRIMARY KEY, "
                f"embedding FLOAT[{EMBED_DIM}] distance_metric=cosine)"
            )
        except sqlite3.OperationalError as e:
            # Extension loaded but vec0 not compiled in — treat as no vec.
            log.warning(f"vec0 create failed: {e}; semantic recall disabled.")
            has_vec = False

    conn.commit()
    return conn, has_vec


# Lazy singleton so import-time failures don't break the whole app.
# Access via `_get_conn()`.
_conn: Optional[sqlite3.Connection] = None
_has_vec: bool = False


def _get_conn() -> tuple[Optional[sqlite3.Connection], bool]:
    global _conn, _has_vec
    if _conn is None:
        try:
            _conn, _has_vec = _connect()
        except Exception as e:
            log.error(f"Memory DB init failed: {e}")
            return None, False
    return _conn, _has_vec


# ── Embedding ──────────────────────────────────────────────────────


def embed(text: str) -> Optional[list[float]]:
    """Return a Gemini text-embedding-004 vector, or None on failure.

    Non-fatal: callers should treat None as "no semantic index, store
    text anyway". We pick Gemini because (a) the user already has an
    API key configured for chat and (b) the free tier covers our
    volume (one embed per chat turn + per distillation output, at
    most a few hundred per day).
    """
    api_key = getattr(config, "GEMINI_API_KEY", "") or ""
    if not api_key:
        # Also look at the providers list in case GEMINI_API_KEY isn't
        # set directly but Gemini is configured through the provider
        # abstraction.
        for prov in getattr(config, "LLM_PROVIDERS", []) or []:
            if prov.get("name", "").lower() == "gemini" and prov.get("api_key"):
                api_key = prov["api_key"]
                break
    if not api_key:
        return None

    try:
        import google.genai as genai
        client = genai.Client(api_key=api_key)
        result = client.models.embed_content(
            model=EMBED_MODEL,
            contents=text,
        )
        # result.embeddings is a list; single input → one entry.
        if result.embeddings:
            vec = list(result.embeddings[0].values)
            if len(vec) == EMBED_DIM:
                return vec
            log.warning(
                f"Embedding dim mismatch: got {len(vec)}, expected {EMBED_DIM}"
            )
    except Exception as e:
        log.warning(f"Embedding failed: {e}")
    return None


def _serialize_vec(vec: list[float]) -> bytes:
    """Pack a float32 vector into sqlite-vec's expected blob format."""
    import sqlite_vec
    return sqlite_vec.serialize_float32(vec)


# ── Public API ─────────────────────────────────────────────────────


def _content_hash(text: str) -> str:
    """Normalized hash for dedup. Strip whitespace so "hi\n" and "hi "
    collapse — most LLM outputs have trivial trailing newline diffs.
    """
    normalized = " ".join(text.split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def remember(
    text: str,
    kind: str = KIND_USER_NOTE,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """Store a text fragment in long-term memory.

    Dedups by content hash — calling remember() twice with the same
    text returns the existing id. Returns None if the DB is
    unavailable (logged, never raises).
    """
    if not text or not text.strip():
        return None
    conn, has_vec = _get_conn()
    if conn is None:
        return None

    h = _content_hash(text)
    now = time.time()

    existing = conn.execute(
        "SELECT id FROM memories WHERE content_hash = ?", (h,)
    ).fetchone()
    if existing:
        return int(existing["id"])

    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    cursor = conn.execute(
        "INSERT INTO memories (text, kind, metadata_json, content_hash, "
        "created_at) VALUES (?, ?, ?, ?, ?)",
        (text, kind, meta_json, h, now),
    )
    mem_id = int(cursor.lastrowid)

    if has_vec:
        vec = embed(text)
        if vec is not None:
            try:
                conn.execute(
                    "INSERT INTO memory_vectors (id, embedding) VALUES (?, ?)",
                    (mem_id, _serialize_vec(vec)),
                )
            except Exception as e:
                log.warning(f"Vector insert failed for mem {mem_id}: {e}")
    conn.commit()
    return mem_id


def recall(
    query: str,
    k: int = 5,
    kinds: Optional[list[str]] = None,
) -> list[dict]:
    """Return up to k memories semantically similar to query.

    Result shape: [{id, text, kind, metadata, distance, created_at}, ...]
    Ordered by ascending distance (most similar first).

    If the vec extension is unavailable, falls back to "most recent
    by kind" so callers still get something useful. If kinds is given,
    results are restricted to those kinds.
    """
    conn, has_vec = _get_conn()
    if conn is None:
        return []

    if not has_vec:
        return _recall_fallback(conn, k, kinds)

    q_vec = embed(query)
    if q_vec is None:
        # No embedding but we have vec: still fall back to recency.
        return _recall_fallback(conn, k, kinds)

    try:
        # sqlite-vec MATCH syntax — oversample by 3x so we can filter
        # by kind after without dropping below k results on common
        # kinds.
        oversample = k * 3 if kinds else k
        rows = conn.execute(
            "SELECT v.id AS id, v.distance AS distance "
            "FROM memory_vectors v "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (_serialize_vec(q_vec), oversample),
        ).fetchall()
    except Exception as e:
        log.warning(f"Vector search failed, falling back to recency: {e}")
        return _recall_fallback(conn, k, kinds)

    if not rows:
        return []

    ids = [r["id"] for r in rows]
    distances = {r["id"]: r["distance"] for r in rows}
    placeholders = ",".join("?" * len(ids))
    sql = (
        f"SELECT id, text, kind, metadata_json, created_at "
        f"FROM memories WHERE id IN ({placeholders})"
    )
    params: list = list(ids)
    if kinds:
        kind_ph = ",".join("?" * len(kinds))
        sql += f" AND kind IN ({kind_ph})"
        params.extend(kinds)
    mem_rows = conn.execute(sql, params).fetchall()

    results: list[dict] = []
    for m in mem_rows:
        results.append(
            {
                "id": int(m["id"]),
                "text": m["text"],
                "kind": m["kind"],
                "metadata": json.loads(m["metadata_json"]) if m["metadata_json"] else {},
                "distance": float(distances.get(m["id"], 0.0)),
                "created_at": float(m["created_at"]),
            }
        )
    # Re-sort by distance (IN clause loses order) and truncate to k.
    results.sort(key=lambda r: r["distance"])
    results = results[:k]

    # Update usage counters for the memories we surfaced.
    if results:
        now = time.time()
        conn.executemany(
            "UPDATE memories SET last_seen_at = ?, "
            "retrieval_count = retrieval_count + 1 WHERE id = ?",
            [(now, r["id"]) for r in results],
        )
        conn.commit()
    return results


def _recall_fallback(
    conn: sqlite3.Connection, k: int, kinds: Optional[list[str]]
) -> list[dict]:
    """Recency-based fallback when vector search isn't available."""
    sql = "SELECT id, text, kind, metadata_json, created_at FROM memories"
    params: list = []
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        sql += f" WHERE kind IN ({placeholders})"
        params.extend(kinds)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(k)
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": int(r["id"]),
            "text": r["text"],
            "kind": r["kind"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else {},
            "distance": 0.0,      # no score in fallback mode
            "created_at": float(r["created_at"]),
        }
        for r in rows
    ]


def forget(memory_id: int) -> bool:
    """Delete a memory by id. Returns True if something was removed."""
    conn, _ = _get_conn()
    if conn is None:
        return False
    cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.execute("DELETE FROM memory_vectors WHERE id = ?", (memory_id,))
    conn.commit()
    return cur.rowcount > 0


def memory_stats() -> dict:
    """Quick status report for debugging / the settings tab."""
    conn, has_vec = _get_conn()
    if conn is None:
        return {"available": False, "has_vec": False}
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    by_kind = dict(
        conn.execute(
            "SELECT kind, COUNT(*) FROM memories GROUP BY kind"
        ).fetchall()
    )
    with_vec = 0
    if has_vec:
        try:
            with_vec = conn.execute(
                "SELECT COUNT(*) FROM memory_vectors"
            ).fetchone()[0]
        except Exception:
            pass
    return {
        "available": True,
        "has_vec": has_vec,
        "total": int(total),
        "by_kind": {k: int(v) for k, v in by_kind.items()},
        "with_vec": int(with_vec),
        "db_path": str(MEMORY_DB),
    }


# ── Context Bus integration ────────────────────────────────────────


def publish_relevant_memories(query: str, k: int = 3) -> int:
    """Recall relevant memories and publish them to the Context Bus.

    Convenience wrapper used by chat/analysis paths. Returns the
    number of memories surfaced — 0 means nothing semantically close
    was found (or embedding failed), so the caller can decide whether
    to fall back to other strategies.
    """
    hits = recall(query, k=k)
    if not hits:
        return 0
    from sentinel.context_bus import get_bus
    bus = get_bus()
    # Publish each hit as a separate entry so APPEND strategy on
    # SOURCE_MEMORY preserves them as distinct items. Each entry
    # includes kind + distance so the LLM can weight source type.
    for h in hits:
        line = (
            f"[{h['kind']} · 相似度 {1.0 - h['distance']:.2f}] {h['text']}"
        )
        bus.publish("memory", line)
    return len(hits)
