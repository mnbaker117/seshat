"""
Tiny helpers for the rate_limit test suite.

Both `snatch_ledger` and `pending_queue` have a foreign-key
constraint on `grabs(id) ON DELETE CASCADE`. Tests that exercise
the ledger and the queue need to insert dummy rows in `grabs` first
to satisfy those constraints. This module exists so that fixture
boilerplate doesn't pollute the actual test files.
"""
from __future__ import annotations

import aiosqlite


async def insert_dummy_grab(
    db: aiosqlite.Connection,
    *,
    torrent_id: str = "1",
    torrent_name: str = "Dummy Book",
    state: str = "submitted",
) -> int:
    """Insert a row into the `grabs` table and return its id.

    Only the columns the rate_limit tests care about are populated;
    everything else gets a defensible default. The returned id is
    what tests pass into `record_grab` / `enqueue`.
    """
    cursor = await db.execute(
        """
        INSERT INTO grabs
            (mam_torrent_id, torrent_name, category, author_blob, state)
        VALUES (?, ?, ?, ?, ?)
        """,
        (torrent_id, torrent_name, "Ebooks - Fantasy", "Test Author", state),
    )
    await db.commit()
    return cursor.lastrowid or 0
