"""Async database engine — Postgres (production) or SQLite (local dev).

If DATABASE_URL env var is set (starts with postgres), uses asyncpg.
Otherwise falls back to aiosqlite from server.config.DB_PATH.

All existing code uses `?` placeholder and `db.execute_fetchone / execute_fetchall`.
The Postgres adapter auto-converts `?` → `$1, $2, ...` so no SQL changes needed.
"""
import os
import re
import logging
from pathlib import Path

log = logging.getLogger("server.db")

DATABASE_URL = os.getenv("DATABASE_URL", "")

_db = None


# ── Postgres adapter ────────────────────────────────────────────────────

def _convert_placeholders(sql: str) -> str:
    """Convert `?` placeholders to `$1, $2, ...` for asyncpg."""
    counter = 0

    def repl(m):
        nonlocal counter
        counter += 1
        return f"${counter}"

    return re.sub(r"\?", repl, sql)


class _PgAdapter:
    """Thin wrapper around asyncpg.Pool that matches aiosqlite interface."""

    def __init__(self, pool):
        self._pool = pool

    async def execute(self, sql, params=None):
        sql = _convert_placeholders(sql)
        args = tuple(params) if params else ()
        return await self._pool.execute(sql, *args)

    async def execute_fetchone(self, sql, params=None):
        sql = _convert_placeholders(sql)
        args = tuple(params) if params else ()
        row = await self._pool.fetchrow(sql, *args)
        return dict(row) if row else None

    async def execute_fetchall(self, sql, params=None):
        sql = _convert_placeholders(sql)
        args = tuple(params) if params else ()
        rows = await self._pool.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def executescript(self, sql):
        """Execute multi-statement SQL (migrations)."""
        async with self._pool.acquire() as conn:
            await conn.execute(sql)

    async def commit(self):
        pass  # Postgres auto-commits; transactions handled per-query

    async def close(self):
        await self._pool.close()


# ── SQLite adapter (wraps aiosqlite to add dict rows) ──────────────────

class _SqliteAdapter:
    """Thin wrapper around aiosqlite to unify interface with Postgres."""

    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql, params=None):
        if params:
            return await self._conn.execute(sql, params)
        return await self._conn.execute(sql)

    async def execute_fetchone(self, sql, params=None):
        if params:
            cursor = await self._conn.execute(sql, params)
        else:
            cursor = await self._conn.execute(sql)
        row = await cursor.fetchone()
        if row is None:
            return None
        # aiosqlite.Row supports dict() if row_factory is set
        return dict(row)

    async def execute_fetchall(self, sql, params=None):
        if params:
            cursor = await self._conn.execute(sql, params)
        else:
            cursor = await self._conn.execute(sql)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def executescript(self, sql):
        await self._conn.executescript(sql)

    async def commit(self):
        await self._conn.commit()

    async def close(self):
        await self._conn.close()


# ── Public interface ────────────────────────────────────────────────────

async def get_db():
    global _db
    if _db is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _db


async def init_db():
    """Initialize database and run migrations."""
    global _db

    if DATABASE_URL.startswith("postgres"):
        import asyncpg

        # Render sets DATABASE_URL with postgres:// but asyncpg needs postgresql://
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        log.info("Connecting to Postgres...")
        pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
        _db = _PgAdapter(pool)
        log.info("Postgres pool ready")
    else:
        import aiosqlite
        from server import config

        log.info(f"Using SQLite: {config.DB_PATH}")
        conn = await aiosqlite.connect(str(config.DB_PATH))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        _db = _SqliteAdapter(conn)

    # Run migrations
    migrations_dir = Path(__file__).parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        sql = sql_file.read_text(encoding="utf-8")
        try:
            await _db.executescript(sql)
            await _db.commit()
        except Exception as e:
            log.warning(f"Migration {sql_file.name}: {e}")

    log.info("Migrations done")


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None
