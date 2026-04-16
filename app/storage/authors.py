"""
Small CRUD helpers for the author taxonomy tables.

Three lists form a strict hierarchy used by the filter + review flows:

    authors_allowed             — filter passes; grabs proceed
    authors_tentative_review    — filter routed to tentative review;
                                  rejected tentative torrents put the
                                  author here for ONE weekly pass of
                                  manual review
    authors_ignored             — filter skips outright; still captured
                                  to ignored_torrents_seen for weekly
                                  "change your mind?" review

Moves between the lists happen via explicit `promote_to_*` functions
so the audit trail is clear. The filter gate doesn't consult
`authors_tentative_review` — tentative routing is driven by the
dispatcher when the ONLY reason for skipping was the author list.
"""
from __future__ import annotations

from typing import Optional

import aiosqlite

from app.filter.normalize import normalize_author


async def _is_in(
    db: aiosqlite.Connection, table: str, normalized: str
) -> bool:
    cursor = await db.execute(
        f"SELECT 1 FROM {table} WHERE normalized = ?", (normalized,)
    )
    return await cursor.fetchone() is not None


async def is_allowed(db: aiosqlite.Connection, name: str) -> bool:
    return await _is_in(db, "authors_allowed", normalize_author(name))


async def is_ignored(db: aiosqlite.Connection, name: str) -> bool:
    return await _is_in(db, "authors_ignored", normalize_author(name))


async def is_tentative_review(db: aiosqlite.Connection, name: str) -> bool:
    return await _is_in(db, "authors_tentative_review", normalize_author(name))


async def add_tentative_review(
    db: aiosqlite.Connection,
    name: str,
    *,
    source: str = "tentative_reject",
) -> bool:
    normalized = normalize_author(name)
    if not normalized:
        return False
    try:
        await db.execute(
            """
            INSERT INTO authors_tentative_review (name, normalized, source)
            VALUES (?, ?, ?)
            """,
            (name.strip(), normalized, source),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def remove_tentative_review(
    db: aiosqlite.Connection, name: str
) -> None:
    await db.execute(
        "DELETE FROM authors_tentative_review WHERE normalized = ?",
        (normalize_author(name),),
    )
    await db.commit()


async def add_ignored(
    db: aiosqlite.Connection, name: str, *, source: str = "manual"
) -> bool:
    normalized = normalize_author(name)
    if not normalized:
        return False
    try:
        await db.execute(
            """
            INSERT INTO authors_ignored (name, normalized, source)
            VALUES (?, ?, ?)
            """,
            (name.strip(), normalized, source),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def promote_tentative_to_allowed(
    db: aiosqlite.Connection, name: str
) -> None:
    normalized = normalize_author(name)
    await db.execute(
        """
        INSERT OR IGNORE INTO authors_allowed (name, normalized, source)
        VALUES (?, ?, ?)
        """,
        (name.strip(), normalized, "tentative_promote"),
    )
    await db.execute(
        "DELETE FROM authors_tentative_review WHERE normalized = ?",
        (normalized,),
    )
    await db.commit()


async def promote_tentative_to_ignored(
    db: aiosqlite.Connection, name: str
) -> None:
    normalized = normalize_author(name)
    await db.execute(
        """
        INSERT OR IGNORE INTO authors_ignored (name, normalized, source)
        VALUES (?, ?, ?)
        """,
        (name.strip(), normalized, "tentative_auto_ignore"),
    )
    await db.execute(
        "DELETE FROM authors_tentative_review WHERE normalized = ?",
        (normalized,),
    )
    await db.commit()


async def list_allowed(
    db: aiosqlite.Connection,
    *,
    search: str = "",
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    return await _list_table(
        db, "authors_allowed", search=search, limit=limit, offset=offset
    )


async def list_ignored(
    db: aiosqlite.Connection,
    *,
    search: str = "",
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    return await _list_table(
        db, "authors_ignored", search=search, limit=limit, offset=offset
    )


async def load_normalized_sets(
    db: aiosqlite.Connection,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return (allowed, ignored) as frozensets of normalized names.

    Used by the filter-config refresh path — the dispatcher's
    FilterConfig needs live allow/ignore membership, and this is
    the cheap bulk-read (two SELECT-normalized queries, ~thousands
    of rows max) that rebuilds both sets at once.

    Empty normalized strings are dropped — they'd never match a
    real normalized announce author anyway and would just waste
    set space.
    """
    allowed_cursor = await db.execute(
        "SELECT normalized FROM authors_allowed"
    )
    allowed_rows = await allowed_cursor.fetchall()
    allowed = frozenset(str(r[0]) for r in allowed_rows if r[0])

    ignored_cursor = await db.execute(
        "SELECT normalized FROM authors_ignored"
    )
    ignored_rows = await ignored_cursor.fetchall()
    ignored = frozenset(str(r[0]) for r in ignored_rows if r[0])

    return allowed, ignored


async def count_allowed(db: aiosqlite.Connection) -> int:
    return await _count_table(db, "authors_allowed")


async def count_ignored(db: aiosqlite.Connection) -> int:
    return await _count_table(db, "authors_ignored")


async def count_tentative_review(db: aiosqlite.Connection) -> int:
    return await _count_table(db, "authors_tentative_review")


async def add_allowed(
    db: aiosqlite.Connection, name: str, *, source: str = "manual"
) -> bool:
    normalized = normalize_author(name)
    if not normalized:
        return False
    try:
        await db.execute(
            """
            INSERT INTO authors_allowed (name, normalized, source)
            VALUES (?, ?, ?)
            """,
            (name.strip(), normalized, source),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def remove_allowed(db: aiosqlite.Connection, name: str) -> int:
    return await _delete_by_normalized(db, "authors_allowed", name)


async def remove_ignored(db: aiosqlite.Connection, name: str) -> int:
    return await _delete_by_normalized(db, "authors_ignored", name)


async def move_allowed_to_ignored(
    db: aiosqlite.Connection, name: str
) -> bool:
    """Atomically remove from allowed + insert into ignored."""
    normalized = normalize_author(name)
    if not normalized:
        return False
    cursor = await db.execute(
        "SELECT name FROM authors_allowed WHERE normalized = ?",
        (normalized,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False
    display = str(row["name"])
    await db.execute(
        "DELETE FROM authors_allowed WHERE normalized = ?", (normalized,)
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO authors_ignored (name, normalized, source)
        VALUES (?, ?, ?)
        """,
        (display, normalized, "manual_move"),
    )
    await db.commit()
    return True


async def move_ignored_to_allowed(
    db: aiosqlite.Connection, name: str
) -> bool:
    normalized = normalize_author(name)
    if not normalized:
        return False
    cursor = await db.execute(
        "SELECT name FROM authors_ignored WHERE normalized = ?",
        (normalized,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False
    display = str(row["name"])
    await db.execute(
        "DELETE FROM authors_ignored WHERE normalized = ?", (normalized,)
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO authors_allowed (name, normalized, source)
        VALUES (?, ?, ?)
        """,
        (display, normalized, "manual_move"),
    )
    await db.commit()
    return True


# ─── Internal helpers ──────────────────────────────────────────


async def _list_table(
    db: aiosqlite.Connection,
    table: str,
    *,
    search: str,
    limit: int,
    offset: int,
) -> list[dict]:
    if search:
        # Case-insensitive substring match against the normalized form,
        # which is itself lowercase + punctuation-collapsed. Falls back
        # to LIKE rather than FTS — the author lists are O(thousands)
        # at most, well below where FTS pays for itself.
        normalized_search = normalize_author(search) or search.lower()
        cursor = await db.execute(
            f"""
            SELECT name, normalized, source, added_at
            FROM {table}
            WHERE normalized LIKE ?
            ORDER BY added_at DESC
            LIMIT ? OFFSET ?
            """,
            (f"%{normalized_search}%", limit, offset),
        )
    else:
        cursor = await db.execute(
            f"""
            SELECT name, normalized, source, added_at
            FROM {table}
            ORDER BY added_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
    rows = await cursor.fetchall()
    return [
        {
            "name": str(r["name"]),
            "normalized": str(r["normalized"]),
            "source": str(r["source"] or ""),
            "added_at": str(r["added_at"] or ""),
        }
        for r in rows
    ]


async def _count_table(db: aiosqlite.Connection, table: str) -> int:
    cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _delete_by_normalized(
    db: aiosqlite.Connection, table: str, name: str
) -> int:
    normalized = normalize_author(name)
    if not normalized:
        return 0
    cursor = await db.execute(
        f"DELETE FROM {table} WHERE normalized = ?", (normalized,)
    )
    await db.commit()
    return cursor.rowcount


async def list_tentative_review(
    db: aiosqlite.Connection,
) -> list[dict]:
    cursor = await db.execute(
        """
        SELECT name, normalized, source, added_at
        FROM authors_tentative_review
        ORDER BY added_at DESC
        """
    )
    rows = await cursor.fetchall()
    return [
        {
            "name": r["name"],
            "normalized": r["normalized"],
            "source": r["source"],
            "added_at": r["added_at"],
        }
        for r in rows
    ]
