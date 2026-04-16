"""
Unit tests for the post-download completion detector.

Tests target `check_for_completions()` directly with a temp database
and synthetic qBit snapshots.
"""
from app.database import get_db
from app.orchestrator.download_watcher import (
    TorrentSnap,
    check_for_completions,
)
from app.storage import grabs as grabs_storage
from app.storage import pipeline as pipeline_storage


async def _insert_submitted_grab(db, torrent_id: str, qbit_hash: str) -> int:
    """Insert a grab in submitted state with a qbit_hash."""
    grab_id = await grabs_storage.create_grab(
        db,
        announce_id=None,
        mam_torrent_id=torrent_id,
        torrent_name=f"Book {torrent_id}",
        category="ebooks fantasy",
        author_blob="Test Author",
        state=grabs_storage.STATE_SUBMITTED,
    )
    await grabs_storage.set_state(
        db, grab_id, grabs_storage.STATE_SUBMITTED, qbit_hash=qbit_hash
    )
    return grab_id


class TestCheckForCompletions:
    async def test_detects_uploading_as_complete(self, temp_db):
        db = await get_db()
        try:
            grab_id = await _insert_submitted_grab(db, "111", "hash_aaa")
            snapshot = {"hash_aaa": TorrentSnap(state="uploading", save_path="/dl/book1")}

            events = await check_for_completions(db, snapshot)

            assert len(events) == 1
            assert events[0].grab_id == grab_id
            assert events[0].qbit_hash == "hash_aaa"
            assert events[0].save_path == "/dl/book1"

            # Grab should be in "downloaded" state now.
            grab = await grabs_storage.get_grab(db, grab_id)
            assert grab.state == grabs_storage.STATE_DOWNLOADED

            # Pipeline run should exist.
            run = await pipeline_storage.find_by_grab_id(db, grab_id)
            assert run is not None
            assert run.state == pipeline_storage.PIPE_STAGED
            assert run.source_path == "/dl/book1"
        finally:
            await db.close()

    async def test_ignores_still_downloading(self, temp_db):
        db = await get_db()
        try:
            await _insert_submitted_grab(db, "222", "hash_bbb")
            snapshot = {"hash_bbb": TorrentSnap(state="downloading", save_path="/dl/book2")}

            events = await check_for_completions(db, snapshot)
            assert len(events) == 0

            # Grab should still be in submitted state.
            grab = await grabs_storage.get_grab(db, 1)
            assert grab.state == grabs_storage.STATE_SUBMITTED
        finally:
            await db.close()

    async def test_ignores_missing_from_qbit(self, temp_db):
        db = await get_db()
        try:
            await _insert_submitted_grab(db, "333", "hash_ccc")
            snapshot = {}  # torrent not in qBit

            events = await check_for_completions(db, snapshot)
            assert len(events) == 0
        finally:
            await db.close()

    async def test_skips_already_processed(self, temp_db):
        db = await get_db()
        try:
            grab_id = await _insert_submitted_grab(db, "444", "hash_ddd")
            # Simulate already having a pipeline run.
            await pipeline_storage.create_run(
                db, grab_id=grab_id, qbit_hash="hash_ddd"
            )
            snapshot = {"hash_ddd": TorrentSnap(state="uploading", save_path="/dl/book4")}

            events = await check_for_completions(db, snapshot)
            assert len(events) == 0
        finally:
            await db.close()

    async def test_detects_multiple_completions(self, temp_db):
        db = await get_db()
        try:
            await _insert_submitted_grab(db, "555", "hash_eee")
            await _insert_submitted_grab(db, "666", "hash_fff")
            snapshot = {
                "hash_eee": TorrentSnap(state="uploading", save_path="/dl/book5"),
                "hash_fff": TorrentSnap(state="pausedUP", save_path="/dl/book6"),
            }

            events = await check_for_completions(db, snapshot)
            assert len(events) == 2
        finally:
            await db.close()

    async def test_various_post_download_states(self, temp_db):
        """All non-downloading qBit states should count as complete."""
        post_dl_states = ["uploading", "forcedUP", "pausedUP", "stalledUP",
                          "checkingUP", "queuedUP", "stoppedUP"]
        db = await get_db()
        try:
            for i, state in enumerate(post_dl_states):
                await _insert_submitted_grab(db, str(700 + i), f"hash_{i}")
            snapshot = {
                f"hash_{i}": TorrentSnap(state=state, save_path=f"/dl/book{i}")
                for i, state in enumerate(post_dl_states)
            }

            events = await check_for_completions(db, snapshot)
            assert len(events) == len(post_dl_states)
        finally:
            await db.close()

    async def test_various_downloading_states_ignored(self, temp_db):
        """All downloading qBit states should NOT trigger completion."""
        dl_states = ["downloading", "forcedDL", "metaDL", "stalledDL",
                     "checkingDL", "queuedDL", "allocating", "moving"]
        db = await get_db()
        try:
            for i, state in enumerate(dl_states):
                await _insert_submitted_grab(db, str(800 + i), f"hash_dl_{i}")
            snapshot = {
                f"hash_dl_{i}": TorrentSnap(state=state, save_path=f"/dl/book{i}")
                for i, state in enumerate(dl_states)
            }

            events = await check_for_completions(db, snapshot)
            assert len(events) == 0
        finally:
            await db.close()
