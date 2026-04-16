"""
Tests for the daily + weekly digest jobs.

Uses a fake ntfy client (monkeypatches app.notify.ntfy.send) to
capture outgoing notifications without making HTTP calls. Verifies
that each digest:
  - queries the right tables
  - aggregates counts/samples correctly
  - sends at least one notification when there's content
"""
import pytest

from app.database import get_db
from app.notify import digests, ntfy
from app.storage import grabs as grabs_storage
from app.storage import tentative as tentative_storage
from app.storage import calibre_adds as calibre_adds_storage


@pytest.fixture
def captured_ntfy(monkeypatch):
    """Replace ntfy.send with a capture list."""
    sent: list[dict] = []

    async def _fake_send(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(ntfy, "send", _fake_send)
    return sent


def _ctx():
    return digests.DigestContext(
        ntfy_url="https://ntfy.sh",
        ntfy_topic="seshat-test",
    )


class TestDailyAccepted:
    async def test_sends_zero_notice_when_empty(self, temp_db, captured_ntfy):
        await digests.daily_accepted(_ctx())
        assert len(captured_ntfy) == 1
        assert "no new books" in captured_ntfy[0]["title"]

    async def test_lists_recent_accepted(self, temp_db, captured_ntfy):
        db = await get_db()
        try:
            await grabs_storage.create_grab(
                db, announce_id=None, mam_torrent_id="1",
                torrent_name="New Book", category="ebooks fantasy",
                author_blob="Fresh Author",
                state=grabs_storage.STATE_SUBMITTED,
            )
        finally:
            await db.close()

        await digests.daily_accepted(_ctx())
        assert len(captured_ntfy) == 1
        assert "1 book(s)" in captured_ntfy[0]["title"]
        assert "New Book" in captured_ntfy[0]["message"]


class TestDailyTentative:
    async def test_silent_when_no_tentative(self, temp_db, captured_ntfy):
        await digests.daily_tentative(_ctx())
        assert captured_ntfy == []

    async def test_sends_when_tentative_exists(self, temp_db, captured_ntfy):
        db = await get_db()
        try:
            await tentative_storage.upsert_tentative(
                db,
                mam_torrent_id="555",
                torrent_name="Maybe Book",
                author_blob="Undecided Author",
                category="Ebooks - Fantasy",
            )
        finally:
            await db.close()

        await digests.daily_tentative(_ctx())
        assert len(captured_ntfy) == 1
        assert "1 new" in captured_ntfy[0]["title"]
        assert "Undecided Author" in captured_ntfy[0]["message"]


class TestDailyIgnored:
    async def test_silent_when_no_ignored(self, temp_db, captured_ntfy):
        await digests.daily_ignored(_ctx())
        assert captured_ntfy == []

    async def test_counts_unique_authors(self, temp_db, captured_ntfy):
        db = await get_db()
        try:
            await tentative_storage.record_ignored_seen(
                db, mam_torrent_id="1", torrent_name="A",
                author_blob="Alpha", category="cat",
            )
            await tentative_storage.record_ignored_seen(
                db, mam_torrent_id="2", torrent_name="B",
                author_blob="Alpha", category="cat",
            )
            await tentative_storage.record_ignored_seen(
                db, mam_torrent_id="3", torrent_name="C",
                author_blob="Beta", category="cat",
            )
        finally:
            await db.close()

        await digests.daily_ignored(_ctx())
        assert len(captured_ntfy) == 1
        assert "3 torrents" in captured_ntfy[0]["title"]
        assert "2 authors" in captured_ntfy[0]["title"]


class TestWeeklyDigest:
    async def test_weekly_includes_additions_and_authors(
        self, temp_db, captured_ntfy
    ):
        db = await get_db()
        try:
            gid = await grabs_storage.create_grab(
                db, announce_id=None, mam_torrent_id="77",
                torrent_name="Weekly Book", category="ebooks fantasy",
                author_blob="Someone",
                state=grabs_storage.STATE_COMPLETE,
            )
            await calibre_adds_storage.record_addition(
                db, grab_id=gid, review_id=None,
                title="Weekly Book", author="Someone", sink_name="cwa",
            )
            await db.execute(
                "INSERT INTO authors_allowed (name, normalized, source) "
                "VALUES (?, ?, ?)",
                ("Fresh One", "fresh one", "auto_train"),
            )
            await db.commit()
        finally:
            await db.close()

        await digests.run_weekly(_ctx())
        assert len(captured_ntfy) == 1
        msg = captured_ntfy[0]["message"]
        assert "Books added to Calibre: 1" in msg
        assert "Authors added to allowed: 1" in msg
        assert "Weekly Book" in msg

    async def test_weekly_auto_promotes_stale_tentative(
        self, temp_db, captured_ntfy
    ):
        db = await get_db()
        try:
            # Stale tentative-review author from 10 days ago.
            await db.execute(
                "INSERT INTO authors_tentative_review (name, normalized, source, added_at) "
                "VALUES (?, ?, ?, datetime('now', '-10 days'))",
                ("Stale Author", "stale author", "tentative_reject"),
            )
            await db.commit()
        finally:
            await db.close()

        await digests.run_weekly(_ctx())

        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM authors_tentative_review"
            )
            row = await cursor.fetchone()
            assert row[0] == 0

            cursor = await db.execute(
                "SELECT COUNT(*) FROM authors_ignored WHERE normalized = ?",
                ("stale author",),
            )
            row = await cursor.fetchone()
            assert row[0] == 1
        finally:
            await db.close()
