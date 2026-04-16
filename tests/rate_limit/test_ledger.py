"""
Unit tests for the snatch ledger.

Each test gets a fresh `temp_db` fixture, opens a connection via
`get_db()`, inserts dummy `grabs` rows to satisfy the FK, and
exercises one slice of the ledger surface.

Coverage targets:
  - record_grab: insert + idempotent re-insert
  - update_seeding: only updates active rows; released rows untouched
  - mark_released: flips state, idempotent on already-released rows
  - count_active / list_active / get_row: read-side correctness
  - reconcile_with_qbit: the load-bearing function — the bridge
    between qBit polling and ledger state. Covers all four cases:
    seedtime reached, removed from qBit, in-progress update, no-op
    on already-released rows.
"""
from app.database import get_db
from app.rate_limit import ledger
from tests.rate_limit._helpers import insert_dummy_grab


# ─── record_grab ─────────────────────────────────────────────


class TestRecordGrab:
    async def test_inserts_active_row(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "abcd1234")

            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.qbit_hash == "abcd1234"
            assert row.seeding_seconds == 0
            assert row.released_at is None
            assert row.released_reason is None
        finally:
            await db.close()

    async def test_idempotent_re_insert_resets_state(self, temp_db):
        # Simulating the post-restart case: Seshat crashed after
        # submitting to qBit but before commit. On restart we
        # re-record the grab; the ledger should accept it cleanly.
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "first_hash")
            await ledger.update_seeding(db, "first_hash", 5000)

            await ledger.record_grab(db, grab_id, "first_hash")
            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.seeding_seconds == 0  # reset to 0
        finally:
            await db.close()


# ─── update_seeding ──────────────────────────────────────────


class TestUpdateSeeding:
    async def test_updates_active_row(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "h1")
            await ledger.update_seeding(db, "h1", 12345)

            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.seeding_seconds == 12345
            assert row.last_check_at is not None
        finally:
            await db.close()

    async def test_does_not_update_released_row(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "h2")
            await ledger.update_seeding(db, "h2", 100)
            await ledger.mark_released(db, grab_id, "seedtime_reached")

            # Now try to update — should be a no-op.
            await ledger.update_seeding(db, "h2", 999999)
            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.seeding_seconds == 100
        finally:
            await db.close()

    async def test_unknown_hash_silent_noop(self, temp_db):
        # An update for a hash we never recorded shouldn't crash.
        db = await get_db()
        try:
            await ledger.update_seeding(db, "never_seen", 1000)
            assert await ledger.count_active(db) == 0
        finally:
            await db.close()


# ─── mark_released ───────────────────────────────────────────


class TestMarkReleased:
    async def test_flips_state(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "h3")
            await ledger.mark_released(db, grab_id, "seedtime_reached")

            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.released_at is not None
            assert row.released_reason == "seedtime_reached"
        finally:
            await db.close()

    async def test_idempotent_on_already_released(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "h4")
            await ledger.mark_released(db, grab_id, "seedtime_reached")

            # Second call with a different reason shouldn't overwrite.
            await ledger.mark_released(db, grab_id, "removed_from_qbit")
            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.released_reason == "seedtime_reached"
        finally:
            await db.close()


# ─── count_active / list_active ──────────────────────────────


class TestActiveQueries:
    async def test_count_excludes_released(self, temp_db):
        db = await get_db()
        try:
            ids = []
            for i in range(5):
                grab_id = await insert_dummy_grab(db, torrent_id=str(i))
                await ledger.record_grab(db, grab_id, f"h{i}")
                ids.append(grab_id)

            assert await ledger.count_active(db) == 5

            await ledger.mark_released(db, ids[0], "seedtime_reached")
            await ledger.mark_released(db, ids[2], "removed_from_qbit")
            assert await ledger.count_active(db) == 3
        finally:
            await db.close()

    async def test_list_active_orders_by_grab_id(self, temp_db):
        db = await get_db()
        try:
            for i in range(3):
                grab_id = await insert_dummy_grab(db, torrent_id=str(i))
                await ledger.record_grab(db, grab_id, f"h{i}")

            rows = await ledger.list_active(db)
            assert len(rows) == 3
            assert rows[0].grab_id < rows[1].grab_id < rows[2].grab_id
        finally:
            await db.close()


# ─── reconcile_with_qbit (the load-bearing function) ─────────


class TestReconcile:
    async def test_seedtime_reached_releases_row(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "h_done")

            # Simulate qBit reporting 73h of seedtime (above the
            # 72h threshold).
            qbit_snapshot = {"h_done": 73 * 3600}
            summary = await ledger.reconcile_with_qbit(
                db, qbit_snapshot, seed_seconds_required=72 * 3600
            )

            assert summary["released_seedtime"] == 1
            assert summary["released_removed"] == 0
            assert await ledger.count_active(db) == 0

            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.released_reason == "seedtime_reached"
        finally:
            await db.close()

    async def test_under_threshold_keeps_active(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "h_partial")

            qbit_snapshot = {"h_partial": 50 * 3600}  # 50h, under 72h
            summary = await ledger.reconcile_with_qbit(
                db, qbit_snapshot, seed_seconds_required=72 * 3600
            )

            assert summary["updated"] == 1
            assert summary["released_seedtime"] == 0
            assert await ledger.count_active(db) == 1
            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.seeding_seconds == 50 * 3600
        finally:
            await db.close()

    async def test_removed_from_qbit_releases_row(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "h_gone")

            # qBit doesn't have it anymore — user removed it.
            summary = await ledger.reconcile_with_qbit(
                db, {}, seed_seconds_required=72 * 3600
            )

            assert summary["released_removed"] == 1
            assert summary["released_seedtime"] == 0
            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.released_reason == "removed_from_qbit"
        finally:
            await db.close()

    async def test_already_released_rows_unchanged(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger.record_grab(db, grab_id, "h_done")
            await ledger.mark_released(db, grab_id, "seedtime_reached")

            # The row is already released. Reconcile should treat it
            # as out-of-scope — not in active list, not updated, not
            # touched. The row's reason should not flip even if the
            # qbit snapshot now lacks the hash.
            summary = await ledger.reconcile_with_qbit(
                db, {}, seed_seconds_required=72 * 3600
            )

            assert summary == {"updated": 0, "released_seedtime": 0, "released_removed": 0}
            row = await ledger.get_row(db, grab_id)
            assert row is not None
            assert row.released_reason == "seedtime_reached"  # unchanged
        finally:
            await db.close()

    async def test_mixed_snapshot(self, temp_db):
        # Combined scenario: one row hits threshold, one is partial,
        # one is removed. Verifies the summary counters are correct
        # and the right rows are released.
        db = await get_db()
        try:
            done_id = await insert_dummy_grab(db, torrent_id="1")
            partial_id = await insert_dummy_grab(db, torrent_id="2")
            gone_id = await insert_dummy_grab(db, torrent_id="3")

            await ledger.record_grab(db, done_id, "done")
            await ledger.record_grab(db, partial_id, "partial")
            await ledger.record_grab(db, gone_id, "gone")

            qbit_snapshot = {
                "done": 80 * 3600,
                "partial": 30 * 3600,
                # "gone" not present
            }
            summary = await ledger.reconcile_with_qbit(
                db, qbit_snapshot, seed_seconds_required=72 * 3600
            )

            assert summary["updated"] == 2  # done + partial
            assert summary["released_seedtime"] == 1
            assert summary["released_removed"] == 1
            assert await ledger.count_active(db) == 1  # just partial

            done_row = await ledger.get_row(db, done_id)
            assert done_row.released_reason == "seedtime_reached"
            gone_row = await ledger.get_row(db, gone_id)
            assert gone_row.released_reason == "removed_from_qbit"
            partial_row = await ledger.get_row(db, partial_id)
            assert partial_row.released_at is None
            assert partial_row.seeding_seconds == 30 * 3600
        finally:
            await db.close()
