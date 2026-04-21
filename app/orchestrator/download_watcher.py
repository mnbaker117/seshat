"""
Post-download completion detector.

`check_for_completions()` scans the qBit snapshot for grabs in
`submitted` state whose torrent has finished downloading (state is
no longer "downloading", "metaDL", "stalledDL", etc.). For each
newly-completed grab, it:

  1. Transitions the grab to `downloaded`
  2. Creates a `pipeline_runs` row in `staged` state
  3. Returns the list of newly-detected completions

This module is called by the budget watcher's tick — not as a
separate polling loop — because both need the same qBit snapshot.
No extra HTTP round-trips.

qBit download states (torrent is still downloading):
  - downloading, forcedDL, metaDL, stalledDL, checkingDL,
    queuedDL, allocating, moving

qBit post-download states (torrent has finished):
  - uploading, forcedUP, pausedUP, stalledUP, checkingUP,
    queuedUP, stoppedUP

We treat any state NOT in the "downloading" set as "download complete".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiosqlite

from app.storage import grabs as grabs_storage
from app.storage import pipeline as pipeline_storage

_log = logging.getLogger("seshat.orchestrator.download_watcher")

# qBit states that mean "still downloading / not yet complete".
_DOWNLOADING_STATES = frozenset({
    "downloading",
    "forcedDL",
    "metaDL",
    "stalledDL",
    "checkingDL",
    "queuedDL",
    "allocating",
    "moving",
    # qBit v5+ uses "stopped" generically
    "stoppedDL",
})


@dataclass(frozen=True)
class CompletionEvent:
    """One detected download completion."""

    grab_id: int
    qbit_hash: str
    torrent_name: str
    save_path: str
    pipeline_run_id: int


async def adopt_orphan_torrents(
    db: aiosqlite.Connection,
    qbit_torrents: list,
) -> int:
    """Create grab rows for torrents qBit has but Seshat doesn't know about.

    Handles the manual-add workflow: user downloads a .torrent from
    MAM and drops it into qBit directly (or any other non-Seshat
    tool puts a torrent in the watched category). Without a grabs
    row the download watcher silently ignores the completion, so
    manually-added books never flow through the pipeline.

    We insert one row per unknown qBit hash with state=submitted and
    qbit_hash set. `check_for_completions()` on the same tick picks
    up any that are already done; ones still downloading get caught
    on a future tick.

    MAM-safe: zero outbound traffic. Pure local observation.

    `mam_torrent_id` is intentionally blank — we didn't fetch this
    from MAM, so there's no authoritative ID to record. Callers that
    need provenance can filter on `category = 'manual_add'`.
    """
    if not qbit_torrents:
        return 0

    cursor = await db.execute(
        "SELECT qbit_hash FROM grabs WHERE qbit_hash IS NOT NULL"
    )
    rows = await cursor.fetchall()
    known = {row["qbit_hash"] for row in rows if row["qbit_hash"]}

    adopted = 0
    for t in qbit_torrents:
        h = getattr(t, "hash", "") or ""
        if not h or h in known:
            continue
        name = getattr(t, "name", "") or f"manual_{h[:12]}"
        grab_id = await grabs_storage.create_grab(
            db,
            announce_id=None,
            mam_torrent_id="",
            torrent_name=name,
            category="manual_add",
            author_blob="",
            state=grabs_storage.STATE_SUBMITTED,
        )
        await grabs_storage.set_state(
            db, grab_id, grabs_storage.STATE_SUBMITTED, qbit_hash=h,
        )
        adopted += 1
        _log.info(
            "download watcher: adopted orphan torrent grab_id=%d hash=%s name=%r",
            grab_id, h[:16], name,
        )

    return adopted


async def check_for_completions(
    db: aiosqlite.Connection,
    qbit_snapshot: dict[str, "TorrentSnap"],
) -> list[CompletionEvent]:
    """Detect grabs whose downloads have completed.

    Args:
        db: Open database connection.
        qbit_snapshot: Map of qbit_hash → TorrentSnap from the latest
                       qBit poll. Each snap has .state and .save_path.

    Returns list of CompletionEvent for newly-detected completions.
    """
    # Find all grabs in "submitted" state that have a qbit_hash.
    cursor = await db.execute(
        """
        SELECT id, announce_id, mam_torrent_id, torrent_name, category,
               author_blob, torrent_file_path, qbit_hash, state, grabbed_at,
               submitted_at, failed_reason
        FROM grabs
        WHERE state = ? AND qbit_hash IS NOT NULL
        """,
        (grabs_storage.STATE_SUBMITTED,),
    )
    rows = await cursor.fetchall()

    events: list[CompletionEvent] = []

    if rows:
        _log.debug(
            "download watcher: checking %d submitted grabs against %d qBit torrents",
            len(rows), len(qbit_snapshot),
        )

    for row in rows:
        grab = grabs_storage._row_to_grab(row)
        if not grab.qbit_hash:
            continue

        snap = qbit_snapshot.get(grab.qbit_hash)
        if snap is None:
            _log.debug(
                "download watcher: grab_id=%d hash=%s not in qBit snapshot",
                grab.id, grab.qbit_hash[:16],
            )
            continue

        if snap.state in _DOWNLOADING_STATES:
            _log.debug(
                "download watcher: grab_id=%d still downloading (state=%s)",
                grab.id, snap.state,
            )
            continue

        # Check if we already have a pipeline run for this grab.
        existing = await pipeline_storage.find_by_grab_id(db, grab.id)
        if existing is not None:
            # Already detected and pipeline started — skip.
            continue

        # Download is complete! Transition the grab and start the pipeline.
        await grabs_storage.set_state(
            db, grab.id, grabs_storage.STATE_DOWNLOADED
        )

        run_id = await pipeline_storage.create_run(
            db,
            grab_id=grab.id,
            qbit_hash=grab.qbit_hash,
            source_path=snap.save_path,
        )

        event = CompletionEvent(
            grab_id=grab.id,
            qbit_hash=grab.qbit_hash,
            torrent_name=grab.torrent_name,
            save_path=snap.save_path,
            pipeline_run_id=run_id,
        )
        events.append(event)
        _log.info(
            "download complete: grab_id=%d %s (hash=%s, path=%s)",
            grab.id, grab.torrent_name, grab.qbit_hash, snap.save_path,
        )

    return events


@dataclass(frozen=True)
class TorrentSnap:
    """Minimal snapshot of one qBit torrent for completion detection."""

    state: str
    save_path: str
