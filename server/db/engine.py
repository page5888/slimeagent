"""SQLite async database engine."""
import aiosqlite
from pathlib import Path
from server import config

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(str(config.DB_PATH))
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def init_db():
    """Run migrations on startup."""
    db = await get_db()
    migrations_dir = Path(__file__).parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        sql = sql_file.read_text(encoding="utf-8")
        await db.executescript(sql)
    await db.commit()


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None
