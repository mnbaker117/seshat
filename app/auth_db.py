"""
Dedicated authentication database for Seshat.

Auth credentials live in a SEPARATE SQLite file from `seshat.db` for
two reasons:

  1. **Independent file permissions.** The auth DB file gets a 0600
     chmod on POSIX without affecting the main seshat.db that the
     user might back up or share with other tooling.
  2. **Simpler backup story.** Operators can snapshot the main DB
     without leaking the password hash, and vice versa.

The file lives at `<data_dir>/seshat_auth.db`. It contains a single
`auth_users` table holding one admin row. Schema is versioned via
`PRAGMA user_version` following the same pattern as `app/database.py`.
"""
import logging
import os
from pathlib import Path

import aiosqlite

from app.runtime import get_data_dir

logger = logging.getLogger("seshat.auth")


_AUTH_DB_FILENAME = "seshat_auth.db"

_AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_login_at REAL,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    failed_login_locked_until REAL
);
"""

_AUTH_MIGRATIONS: list[str] = []


def get_auth_db_path() -> Path:
    return Path(get_data_dir()) / _AUTH_DB_FILENAME


async def get_auth_db() -> aiosqlite.Connection:
    """Open a connection to the auth DB. Caller closes."""
    path = get_auth_db_path()
    db = await aiosqlite.connect(str(path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA busy_timeout=5000")
    return db


async def init_auth_db() -> None:
    """Create the auth DB if missing, ensure schema, tighten perms.

    Idempotent — safe to call on every startup.
    """
    path = get_auth_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    db = await get_auth_db()
    try:
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current_version = row[0] if row else 0
        target_version = len(_AUTH_MIGRATIONS)

        await db.executescript(_AUTH_SCHEMA)
        await db.commit()

        if current_version < target_version:
            logger.info(
                f"Migrating auth database schema: v{current_version} → v{target_version}"
            )
            for i, migration in enumerate(_AUTH_MIGRATIONS):
                if i < current_version:
                    continue
                try:
                    await db.execute(migration)
                except aiosqlite.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" in msg or "already exists" in msg:
                        continue
                    logger.warning(
                        f"Auth migration #{i} failed: {e} (SQL: {migration[:80]}...)"
                    )
            await db.commit()
            await db.execute(f"PRAGMA user_version = {target_version}")
            await db.commit()
    finally:
        await db.close()

    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass
