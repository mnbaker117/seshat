"""
Tests for the `is_omnibus` flag across the INSERT and UPDATE paths in
`_merge_result`, plus the startup `_backfill_omnibus_flag` helper.

Regression context: the standalone INSERT path in `_merge_result` was
not setting `is_omnibus`, even though the series INSERT path always
did. Stoham Baginbott's "Hero Support: Omnibus" (Goodreads emits it
as a standalone — no series tagging) was inserted with
`is_omnibus=0`, then `_title_to_series_pass` linked it to the
"Hero Support" series, where it appeared alongside the real numbered
volumes. Yesterday's `_update_existing` promotion + startup backfill
covered already-mis-flagged rows but did not catch fresh inserts.
"""
from __future__ import annotations

import pytest


@pytest.fixture
async def discovery_db(tmp_path, monkeypatch):
    from app import config as app_config
    from app.discovery import database as disco_db

    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(disco_db, "DATA_DIR", tmp_path)
    disco_db.set_active_library("test")
    await disco_db.init_db("test")
    yield tmp_path
    disco_db.set_active_library(None)


async def _insert_author(name: str) -> int:
    from app.discovery.database import get_db
    from app.metadata.author_names import normalize_author_name
    db = await get_db()
    try:
        cur = await db.execute(
            "INSERT INTO authors (name, sort_name, normalized_name) "
            "VALUES (?, ?, ?)",
            (name, name, normalize_author_name(name)),
        )
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def _book_by_title(author_id: int, title: str) -> dict:
    from app.discovery.database import get_db
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, title, series_id, series_index, is_omnibus "
            "FROM books WHERE author_id = ? AND title = ?",
            (author_id, title),
        )).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ─── Standalone INSERT path ───────────────────────────────────

class TestStandaloneInsertOmnibus:
    async def test_omnibus_in_title_flags_standalone_insert(self, discovery_db):
        """
        The Stoham case: Goodreads emits "Hero Support: Omnibus" as
        a standalone (no series tagging). The standalone INSERT path
        must detect the omnibus pattern and set `is_omnibus=1` so
        the title→series pass doesn't slot it next to real series
        entries.
        """
        from app.discovery.lookup import _merge_result
        from app.discovery.sources.base import AuthorResult, BookResult

        author_id = await _insert_author("Stoham Baginbott")

        result = AuthorResult(
            name="Stoham Baginbott",
            external_id="stoham-1",
            books=[
                BookResult(
                    title="Hero Support: Omnibus",
                    source="goodreads",
                ),
            ],
        )

        await _merge_result(
            author_id=author_id,
            result=result,
            source_name="goodreads",
            languages=["English"],
        )

        row = await _book_by_title(author_id, "Hero Support: Omnibus")
        assert row is not None
        assert row["is_omnibus"] == 1

    async def test_complete_saga_phrase_flags_standalone_insert(self, discovery_db):
        """
        "The Complete Deadland Saga" matches `_RX_OMNIBUS`'s
        "complete <X> saga" arm. No Books N-M token, so the
        upstream `_is_book_set` filter doesn't drop it — it reaches
        the standalone INSERT path and must be flagged there.
        """
        from app.discovery.lookup import _merge_result
        from app.discovery.sources.base import AuthorResult, BookResult

        author_id = await _insert_author("Joshua Guess")

        result = AuthorResult(
            name="Joshua Guess",
            external_id="guess-1",
            books=[
                BookResult(
                    title="The Complete Deadland Saga",
                    source="hardcover",
                ),
            ],
        )

        await _merge_result(
            author_id=author_id,
            result=result,
            source_name="hardcover",
            languages=["English"],
        )

        row = await _book_by_title(author_id, "The Complete Deadland Saga")
        assert row is not None
        assert row["is_omnibus"] == 1

    async def test_non_omnibus_standalone_stays_zero(self, discovery_db):
        """
        Sanity: ordinary standalone titles must still have
        `is_omnibus=0`.
        """
        from app.discovery.lookup import _merge_result
        from app.discovery.sources.base import AuthorResult, BookResult

        author_id = await _insert_author("Stoham Baginbott")

        result = AuthorResult(
            name="Stoham Baginbott",
            external_id="stoham-1",
            books=[
                BookResult(
                    title="Stay at Home Hero",
                    source="hardcover",
                ),
            ],
        )

        await _merge_result(
            author_id=author_id,
            result=result,
            source_name="hardcover",
            languages=["English"],
        )

        row = await _book_by_title(author_id, "Stay at Home Hero")
        assert row is not None
        assert row["is_omnibus"] == 0


# ─── Backfill (startup helper) ────────────────────────────────

class TestBackfillOmnibus:
    async def test_backfill_flips_matching_zero_rows(self, discovery_db):
        """
        Pre-existing rows (Calibre-synced or inserted before the regex
        gained a keyword) at `is_omnibus=0` get flipped on the next
        startup pass.
        """
        from app.discovery.database import _backfill_omnibus_flag, get_db

        author_id = await _insert_author("Stoham Baginbott")
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO books (title, author_id, source, owned, "
                "is_omnibus, series_index) VALUES (?, ?, 'calibre', 1, 0, 1.0)",
                ("Hero Support: Omnibus", author_id),
            )
            await db.execute(
                "INSERT INTO books (title, author_id, source, owned, "
                "is_omnibus) VALUES (?, ?, 'hardcover', 0, 0)",
                ("Stay at Home Hero", author_id),
            )
            await db.commit()
            touched = await _backfill_omnibus_flag(db)
        finally:
            await db.close()

        assert touched == 1
        omni = await _book_by_title(author_id, "Hero Support: Omnibus")
        assert omni["is_omnibus"] == 1
        assert omni["series_index"] is None
        plain = await _book_by_title(author_id, "Stay at Home Hero")
        assert plain["is_omnibus"] == 0

    async def test_backfill_is_idempotent(self, discovery_db):
        """Second run touches nothing — already-flagged rows are skipped."""
        from app.discovery.database import _backfill_omnibus_flag, get_db

        author_id = await _insert_author("Stoham Baginbott")
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO books (title, author_id, source, owned, "
                "is_omnibus) VALUES (?, ?, 'calibre', 1, 0)",
                ("Amazonian Master Omnibus", author_id),
            )
            await db.commit()
            first = await _backfill_omnibus_flag(db)
            second = await _backfill_omnibus_flag(db)
        finally:
            await db.close()

        assert first == 1
        assert second == 0
