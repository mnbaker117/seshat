"""
Unit tests for the post-download completion detector.

Tests target `check_for_completions()` directly with a temp database
and synthetic qBit snapshots.
"""
from dataclasses import dataclass

from app.database import get_db
from app.orchestrator.download_watcher import (
    TorrentSnap,
    adopt_orphan_torrents,
    check_for_completions,
)
from app.storage import grabs as grabs_storage
from app.storage import pipeline as pipeline_storage


@dataclass
class _FakeQbitTorrent:
    """Stand-in for the real QbitTorrent snapshot fields used by the adopter."""
    hash: str
    name: str
    added_on: int = 0


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


class TestAdoptOrphanTorrents:
    """Fix: manually-added qBit torrents (no grabs row) get adopted so
    the pipeline can process them when they finish."""

    async def test_adopts_unknown_hashes(self, temp_db):
        db = await get_db()
        try:
            torrents = [
                _FakeQbitTorrent(hash="orphan_aaa", name="Manual Book A"),
                _FakeQbitTorrent(hash="orphan_bbb", name="Manual Book B"),
            ]
            adopted = await adopt_orphan_torrents(db, torrents, adoption_cutoff=0)
            assert adopted == 2

            # Both grabs should exist in submitted state with qbit_hash set.
            cursor = await db.execute(
                "SELECT qbit_hash, state, torrent_name, category, mam_torrent_id "
                "FROM grabs ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert len(rows) == 2
            assert rows[0]["qbit_hash"] == "orphan_aaa"
            assert rows[0]["state"] == grabs_storage.STATE_SUBMITTED
            assert rows[0]["torrent_name"] == "Manual Book A"
            assert rows[0]["category"] == "manual_add"
            # mam_torrent_id blank — we didn't pull this from MAM.
            assert rows[0]["mam_torrent_id"] == ""
        finally:
            await db.close()

    async def test_skips_known_hashes(self, temp_db):
        db = await get_db()
        try:
            # Pre-existing grab for this hash.
            await _insert_submitted_grab(db, "mam_111", "known_hash")
            torrents = [
                _FakeQbitTorrent(hash="known_hash", name="Already Tracked"),
                _FakeQbitTorrent(hash="orphan_new", name="New Manual Add"),
            ]
            adopted = await adopt_orphan_torrents(db, torrents, adoption_cutoff=0)
            assert adopted == 1  # only orphan_new

            cursor = await db.execute("SELECT COUNT(*) as cnt FROM grabs")
            row = await cursor.fetchone()
            assert row["cnt"] == 2  # one pre-existing + one adopted
        finally:
            await db.close()

    async def test_empty_list_noop(self, temp_db):
        db = await get_db()
        try:
            adopted = await adopt_orphan_torrents(db, [], adoption_cutoff=0)
            assert adopted == 0
        finally:
            await db.close()

    async def test_cutoff_skips_pre_existing_torrents(self, temp_db):
        """Grandfather line: torrents added before `adoption_cutoff` are
        silently skipped. This is the fix for the cascade bug — without
        it, the first tick after deploying the adopter would re-adopt
        every pre-existing torrent in the watch category (thousands on
        a long-running qBit instance), flooding the review queue.
        """
        db = await get_db()
        try:
            cutoff = 1_700_000_000  # a fixed Unix timestamp
            torrents = [
                _FakeQbitTorrent(
                    hash="old_aaa", name="Pre-existing",
                    added_on=cutoff - 86400,
                ),
                _FakeQbitTorrent(
                    hash="new_bbb", name="Fresh Manual Add",
                    added_on=cutoff + 3600,
                ),
                _FakeQbitTorrent(
                    hash="exact_ccc", name="Added exactly at cutoff",
                    added_on=cutoff,
                ),
            ]
            adopted = await adopt_orphan_torrents(
                db, torrents, adoption_cutoff=cutoff,
            )
            assert adopted == 2  # fresh + exact-boundary, not pre-existing

            cursor = await db.execute(
                "SELECT qbit_hash FROM grabs ORDER BY id"
            )
            rows = await cursor.fetchall()
            hashes = {r["qbit_hash"] for r in rows}
            assert hashes == {"new_bbb", "exact_ccc"}
        finally:
            await db.close()

    async def test_cutoff_zero_disables_filter(self, temp_db):
        """`adoption_cutoff=0` disables the time filter (tests + backward
        compat). All unknown torrents get adopted regardless of added_on.
        """
        db = await get_db()
        try:
            torrents = [
                _FakeQbitTorrent(
                    hash="ancient", name="Very Old",
                    added_on=1,  # effectively "forever ago"
                ),
            ]
            adopted = await adopt_orphan_torrents(
                db, torrents, adoption_cutoff=0,
            )
            assert adopted == 1
        finally:
            await db.close()

    async def test_skips_torrents_without_hash(self, temp_db):
        db = await get_db()
        try:
            torrents = [
                _FakeQbitTorrent(hash="", name="No Hash"),
                _FakeQbitTorrent(hash="orphan_ccc", name="Valid"),
            ]
            adopted = await adopt_orphan_torrents(db, torrents, adoption_cutoff=0)
            assert adopted == 1
        finally:
            await db.close()

    async def test_adopted_row_picked_up_by_completion_check(self, temp_db):
        """Integration: adopt a torrent that's already finished, then the
        completion check on the same tick should fire the pipeline."""
        db = await get_db()
        try:
            torrents = [
                _FakeQbitTorrent(hash="orphan_done", name="Finished Manual Add"),
            ]
            await adopt_orphan_torrents(db, torrents, adoption_cutoff=0)

            snapshot = {
                "orphan_done": TorrentSnap(
                    state="stalledUP", save_path="/dl/finished",
                ),
            }
            events = await check_for_completions(db, snapshot)
            assert len(events) == 1
            assert events[0].qbit_hash == "orphan_done"
            assert events[0].torrent_name == "Finished Manual Add"
        finally:
            await db.close()
