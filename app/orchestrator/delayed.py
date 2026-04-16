"""
Delayed-torrents folder rotation.

When the snatch queue is full and a new grab arrives, we don't want
to drop the new grab on the floor — the queue is FIFO, so newer
announces are often the ones the user cares about most right now.
Instead, we rotate:

    1. Pop the OLDEST queued grab
    2. Re-fetch its .torrent bytes from MAM (the queue doesn't cache
       bytes — see the rationale in `rate_limit/queue.py`)
    3. Write the .torrent file to `delayed_torrents_path/`
    4. Remove the popped grab's ledger/state entries
    5. Return True so the caller can re-check queue capacity and
       enqueue the new grab

The delayed folder is a "dead-drop" the user's future UI will scan
and offer a "push back to queue" action for. We deliberately don't
track delayed files in the database (user decision #4) — the
filesystem IS the queue. No migrations, no orphan cleanup.

Filename convention: `<grab_id>_<mam_torrent_id>.torrent`. The
grab id comes first so ls sort order matches insertion order, and
the torrent id is included so the user can locate the MAM page
without crossing back to the DB.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Protocol

import aiosqlite

from app.mam.grab import GrabResult
from app.rate_limit import queue as queue_mod
from app.storage import grabs as grabs_storage

_log = logging.getLogger("seshat.orchestrator.delayed")


class _FetchFn(Protocol):
    async def __call__(
        self, torrent_id: str, token: str, *, use_fl_wedge: bool = False
    ) -> GrabResult: ...


async def rotate_oldest_to_delayed(
    db: aiosqlite.Connection,
    *,
    delayed_path: str,
    fetch_torrent: _FetchFn,
    mam_token: str,
) -> Optional[int]:
    """Pop the oldest queued grab and park it in the delayed folder.

    Returns the evicted grab_id on success, or None if:
      - the queue is empty
      - the delayed_path isn't configured
      - the MAM fetch failed
      - the disk write failed

    On any failure path the grab is restored to its previous state so
    we don't silently lose it.
    """
    if not delayed_path:
        _log.debug("rotate_oldest_to_delayed: delayed_path not configured")
        return None

    # Pop ORDERS by priority desc, then queued_at asc, which is NOT
    # pure FIFO if any grab has non-zero priority. For rotation we
    # want the LOWEST-priority, OLDEST grab — the one the user is
    # least likely to miss. Query explicitly.
    cursor = await db.execute(
        """
        SELECT grab_id FROM pending_queue
        ORDER BY priority ASC, queued_at ASC
        LIMIT 1
        """
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    evict_id = int(row["grab_id"])

    grab = await grabs_storage.get_grab(db, evict_id)
    if grab is None:
        # Orphan — just drop the queue row.
        await queue_mod.remove(db, evict_id)
        return None

    try:
        result = await fetch_torrent(grab.mam_torrent_id, mam_token)
    except Exception:
        _log.exception(
            "rotate_oldest_to_delayed: fetch raised for grab_id=%d tid=%s",
            evict_id, grab.mam_torrent_id,
        )
        return None

    if not result.success or not result.torrent_bytes:
        _log.warning(
            "rotate_oldest_to_delayed: fetch failed grab_id=%d (%s)",
            evict_id, result.failure_kind,
        )
        return None

    try:
        folder = Path(delayed_path)
        folder.mkdir(parents=True, exist_ok=True)
        # Keep the name filesystem-safe — MAM torrent names can
        # contain slashes, quotes, etc. We only use the ID + grab_id
        # to avoid any sanitization bugs.
        dest = folder / f"{evict_id}_{grab.mam_torrent_id}.torrent"
        dest.write_bytes(result.torrent_bytes)
    except Exception:
        _log.exception(
            "rotate_oldest_to_delayed: write failed grab_id=%d", evict_id
        )
        return None

    # Remove from the pending_queue and mark the grab as failed-unknown
    # with a clear reason. We don't want to leave it in pending_queue
    # state (the budget watcher would try to drain it). A dedicated
    # STATE_DELAYED could be added later if the UI needs the distinction.
    await queue_mod.remove(db, evict_id)
    await grabs_storage.set_state(
        db, evict_id, grabs_storage.STATE_FAILED_UNKNOWN,
        failed_reason="rotated to delayed folder",
    )

    _log.info(
        "rotate_oldest_to_delayed: parked grab_id=%d → %s",
        evict_id, dest,
    )
    return evict_id
