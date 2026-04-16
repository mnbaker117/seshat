"""
CRUD for the `calibre_additions` counter table.

One row per successful sink delivery. Used by daily/weekly digests
to report how many books landed in Calibre without reparsing the
full pipeline_runs history.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import aiosqlite


@dataclass(frozen=True)
class CalibreAdditionRow:
    id: int
    grab_id: int
    review_id: Optional[int]
    title: Optional[str]
    author: Optional[str]
    sink_name: Optional[str]
    added_at: str
    was_timeout: bool


async def record_addition(
    db: aiosqlite.Connection,
    *,
    grab_id: int,
    review_id: Optional[int],
    title: Optional[str],
    author: Optional[str],
    sink_name: Optional[str],
    was_timeout: bool = False,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO calibre_additions
            (grab_id, review_id, title, author, sink_name, was_timeout)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (grab_id, review_id, title, author, sink_name, 1 if was_timeout else 0),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def count_since(
    db: aiosqlite.Connection, *, hours: int
) -> int:
    cursor = await db.execute(
        """
        SELECT COUNT(*) FROM calibre_additions
        WHERE added_at >= datetime('now', ?)
        """,
        (f"-{int(hours)} hours",),
    )
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def list_since(
    db: aiosqlite.Connection, *, hours: int, limit: int = 500
) -> list[CalibreAdditionRow]:
    cursor = await db.execute(
        """
        SELECT id, grab_id, review_id, title, author, sink_name,
               added_at, was_timeout
        FROM calibre_additions
        WHERE added_at >= datetime('now', ?)
        ORDER BY added_at DESC
        LIMIT ?
        """,
        (f"-{int(hours)} hours", limit),
    )
    rows = await cursor.fetchall()
    return [
        CalibreAdditionRow(
            id=int(r["id"]),
            grab_id=int(r["grab_id"]),
            review_id=r["review_id"],
            title=r["title"],
            author=r["author"],
            sink_name=r["sink_name"],
            added_at=str(r["added_at"] or ""),
            was_timeout=bool(r["was_timeout"]),
        )
        for r in rows
    ]
