"""
Delayed-torrents folder rotation tests.

When the queue is full and a new grab arrives, the oldest queued
grab should be evicted to disk and the new grab should take its slot.
"""
from app.database import get_db
from app.mam.grab import GrabResult
from app.orchestrator.delayed import rotate_oldest_to_delayed
from app.rate_limit import queue as queue_mod
from app.storage import grabs as grabs_storage


async def _make_queued_grab(
    db, *, mam_id: str, name: str = "Old Book"
) -> int:
    gid = await grabs_storage.create_grab(
        db, announce_id=None, mam_torrent_id=mam_id,
        torrent_name=name, category="ebooks fantasy",
        author_blob="Author", state=grabs_storage.STATE_PENDING_QUEUE,
    )
    await queue_mod.enqueue(db, gid)
    return gid


class TestDelayedRotation:
    async def test_rotates_oldest_to_disk(self, temp_db, tmp_path):
        db = await get_db()
        try:
            old_id = await _make_queued_grab(db, mam_id="111")

            async def fake_fetch(torrent_id, token, *, use_fl_wedge=False):
                assert torrent_id == "111"
                return GrabResult(
                    success=True,
                    torrent_bytes=b"d4:name4:fakee",
                )

            delayed = tmp_path / "delayed"
            evicted = await rotate_oldest_to_delayed(
                db,
                delayed_path=str(delayed),
                fetch_torrent=fake_fetch,
                mam_token="t",
            )
            assert evicted == old_id

            # File written to delayed dir.
            files = list(delayed.glob("*.torrent"))
            assert len(files) == 1
            assert "111" in files[0].name
            assert files[0].read_bytes() == b"d4:name4:fakee"

            # Queue now empty.
            assert await queue_mod.size(db) == 0

            # Grab row marked as failed with the reason.
            grab = await grabs_storage.get_grab(db, old_id)
            assert grab.state == grabs_storage.STATE_FAILED_UNKNOWN
            assert "delayed folder" in (grab.failed_reason or "")
        finally:
            await db.close()

    async def test_empty_queue_returns_none(self, temp_db, tmp_path):
        db = await get_db()
        try:
            async def fake_fetch(*a, **kw):
                raise AssertionError("should not fetch")

            evicted = await rotate_oldest_to_delayed(
                db,
                delayed_path=str(tmp_path / "delayed"),
                fetch_torrent=fake_fetch,
                mam_token="t",
            )
            assert evicted is None
        finally:
            await db.close()

    async def test_no_path_disables(self, temp_db):
        db = await get_db()
        try:
            await _make_queued_grab(db, mam_id="222")

            async def fake_fetch(*a, **kw):
                raise AssertionError("should not fetch")

            evicted = await rotate_oldest_to_delayed(
                db, delayed_path="", fetch_torrent=fake_fetch, mam_token="t"
            )
            assert evicted is None
            assert await queue_mod.size(db) == 1
        finally:
            await db.close()

    async def test_fetch_failure_preserves_queue(self, temp_db, tmp_path):
        db = await get_db()
        try:
            gid = await _make_queued_grab(db, mam_id="333")

            async def fake_fetch(*a, **kw):
                return GrabResult(
                    success=False, failure_kind="cookie_expired",
                    failure_detail="nope",
                )

            evicted = await rotate_oldest_to_delayed(
                db,
                delayed_path=str(tmp_path / "delayed"),
                fetch_torrent=fake_fetch,
                mam_token="t",
            )
            assert evicted is None
            # Grab still in queue, untouched.
            assert await queue_mod.size(db) == 1
            grab = await grabs_storage.get_grab(db, gid)
            assert grab.state == grabs_storage.STATE_PENDING_QUEUE
        finally:
            await db.close()
