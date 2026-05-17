"""
Tests for the v2.16.0 Data Hygiene action (`app.discovery.hygiene`).

Coverage:
  - Job 1 deletes zero-book authors and zero-book series, preserves
    `authors_allowed` by name, and is idempotent on a second pass.
  - Job 2 (Hardcover identifier backfill) early-returns when no API
    key, and COALESCE-fills missing IDs from a stubbed Hardcover
    `book_mappings` response when present.
  - Job 4's identifier-keyed merge pass folds two rows sharing a
    `goodreads_id` into a single row, COALESCE-filling identity
    columns from the loser onto the winner.
  - The chain runs to completion on an empty library and returns a
    zero-stats dict (idempotency on a clean DB).

These tests use the same `merge_dbs`-style fixture as
`test_book_merge.py`: monkeypatch `DATA_DIR` to a tmp_path, init
both the pipeline DB and one per-library discovery DB, and drive
the helpers directly. `state._discovered_libraries` is monkeypatched
per-test so the coordinator finds the test library.
"""
from __future__ import annotations

import pytest

from app import state
from app.discovery import database as disco_db
from app.discovery import hygiene


@pytest.fixture
async def hygiene_dbs(tmp_path, monkeypatch):
    """Tmp pipeline + per-library discovery DB with `_discovered_libraries`
    pre-populated for the coordinator's loop.

    Yields the discovery connection (most tests only touch it
    directly); the pipeline DB lives at DATA_DIR/seshat.db for the
    `authors_allowed` paths to find.
    """
    from app import config as app_config
    from app import database as pipeline_database

    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(disco_db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_config, "APP_DB_PATH", tmp_path / "seshat.db")
    monkeypatch.setattr(pipeline_database, "APP_DB_PATH", tmp_path / "seshat.db")

    await pipeline_database.init_db()
    disco_db.set_active_library("testlib")
    await disco_db.init_db("testlib")

    # Wire the coordinator's library registry so cross-library
    # iteration finds the test library exactly once.
    monkeypatch.setattr(
        state, "_discovered_libraries",
        [{"slug": "testlib", "name": "Test", "content_type": "ebook"}],
    )

    db = await disco_db.get_db("testlib")
    try:
        yield db
    finally:
        await db.close()
        disco_db.set_active_library(None)


async def _insert_author(db, name: str, **fields) -> int:
    from app.metadata.author_names import normalize_author_name

    cols = {
        "name": name,
        "sort_name": name,
        "normalized_name": normalize_author_name(name),
    }
    cols.update(fields)
    keys = list(cols.keys())
    cur = await db.execute(
        f"INSERT INTO authors ({', '.join(keys)}) "
        f"VALUES ({', '.join('?' * len(keys))})",
        list(cols.values()),
    )
    await db.commit()
    return cur.lastrowid


async def _insert_book(db, **fields) -> int:
    defaults = {
        "title": "Untitled",
        "author_id": 1,
        "source": "goodreads",
        "owned": 0,
        "hidden": 0,
    }
    defaults.update(fields)
    cols = list(defaults.keys())
    cur = await db.execute(
        f"INSERT INTO books ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))})",
        list(defaults.values()),
    )
    await db.commit()
    return cur.lastrowid


# ─── Job 1 — Empty cleanup ──────────────────────────────────────────


class TestEmptyCleanup:
    async def test_deletes_zero_book_authors(self, hygiene_dbs):
        db = hygiene_dbs
        a_with_book = await _insert_author(db, "Has Book")
        await _insert_book(db, author_id=a_with_book, title="A real book")
        a_empty = await _insert_author(db, "Empty Author")

        stats = hygiene._zero_stats()
        await hygiene.job_empty_cleanup("testlib", stats)

        # Empty author gone; the populated one stayed.
        remaining = await (await db.execute(
            "SELECT id FROM authors ORDER BY id"
        )).fetchall()
        ids = {r["id"] for r in remaining}
        assert a_with_book in ids
        assert a_empty not in ids
        assert stats["deleted_authors"] == 1
        assert stats["errors"] == []

    async def test_preserves_authors_allowed_by_name(self, hygiene_dbs):
        """An author with 0 books whose normalized name is on the
        `authors_allowed` table MUST survive cleanup — that allow-
        list is the user's authorial-allowlist of record."""
        db = hygiene_dbs
        protected = await _insert_author(db, "Allowed Person")
        unprotected = await _insert_author(db, "Random Stub")

        # Seed authors_allowed with the protected name.
        from app.database import get_db as get_pipeline_db
        from app.metadata.author_names import normalize_author_name

        pdb = await get_pipeline_db()
        try:
            await pdb.execute(
                "INSERT INTO authors_allowed (name, normalized, source) "
                "VALUES (?, ?, 'manual')",
                ("Allowed Person", normalize_author_name("Allowed Person")),
            )
            await pdb.commit()
        finally:
            await pdb.close()

        stats = hygiene._zero_stats()
        await hygiene.job_empty_cleanup("testlib", stats)

        survivors = await (await db.execute(
            "SELECT id FROM authors"
        )).fetchall()
        ids = {r["id"] for r in survivors}
        assert protected in ids, "authors_allowed name must be kept"
        assert unprotected not in ids
        assert stats["deleted_authors"] == 1

    async def test_idempotent_second_pass(self, hygiene_dbs):
        """Re-running over a clean DB deletes nothing."""
        db = hygiene_dbs
        a = await _insert_author(db, "Has Book")
        await _insert_book(db, author_id=a)

        stats1 = hygiene._zero_stats()
        await hygiene.job_empty_cleanup("testlib", stats1)
        stats2 = hygiene._zero_stats()
        await hygiene.job_empty_cleanup("testlib", stats2)
        assert stats2["deleted_authors"] == 0
        assert stats2["deleted_series"] == 0

    async def test_deletes_orphan_series(self, hygiene_dbs):
        """A series row whose every book has been removed gets
        cleaned up by `cleanup_empty_series`."""
        db = hygiene_dbs
        a = await _insert_author(db, "X")
        await db.execute(
            "INSERT INTO series (id, name, author_id) VALUES (?, ?, ?)",
            (1, "Orphan", a),
        )
        await db.commit()

        stats = hygiene._zero_stats()
        await hygiene.job_empty_cleanup("testlib", stats)
        assert stats["deleted_series"] >= 1
        leftover = await (await db.execute(
            "SELECT COUNT(*) AS c FROM series"
        )).fetchone()
        assert leftover["c"] == 0


# ─── Job 2 — Hardcover identifier backfill ──────────────────────────


class TestHardcoverIdBackfill:
    async def test_no_api_key_skips(self, hygiene_dbs, monkeypatch):
        """No API key configured → job exits cleanly and never
        touches a HardcoverSource."""
        db = hygiene_dbs
        a = await _insert_author(db, "X")
        await _insert_book(
            db, author_id=a, hardcover_id="42", goodreads_id=None,
        )

        # Empty settings and no secrets store entry.
        from app import config
        monkeypatch.setattr(
            config, "load_settings",
            lambda: {"hardcover_api_key": ""},
        )
        from app.discovery import hygiene as hyg_mod
        monkeypatch.setattr(hyg_mod, "load_settings", config.load_settings)

        stats = hygiene._zero_stats()
        await hygiene.job_hardcover_id_backfill("testlib", stats)
        assert stats["books_backfilled"] == 0
        row = await (await db.execute(
            "SELECT goodreads_id FROM books WHERE hardcover_id='42'"
        )).fetchone()
        assert row["goodreads_id"] is None

    async def test_stamps_missing_ids_from_book_mappings(
        self, hygiene_dbs, monkeypatch,
    ):
        """When the stubbed Hardcover response carries a Goodreads
        mapping for a book missing `goodreads_id`, the column gets
        COALESCE-filled. Existing non-null values stay untouched."""
        db = hygiene_dbs
        a = await _insert_author(db, "X")
        # Book A is fully missing all three xids — should get all
        # three stamped.
        book_a = await _insert_book(
            db, author_id=a, title="Book A", hardcover_id="100",
        )
        # Book B already has goodreads_id set — COALESCE must not
        # clobber it.
        book_b = await _insert_book(
            db, author_id=a, title="Book B", hardcover_id="200",
            goodreads_id="existing-gr-id",
        )

        # Stub the settings load to give us an API key.
        from app import config
        monkeypatch.setattr(
            config, "load_settings",
            lambda: {"hardcover_api_key": "test-key"},
        )
        from app.discovery import hygiene as hyg_mod
        monkeypatch.setattr(hyg_mod, "load_settings", config.load_settings)

        # Stub HardcoverSource so its `_query` returns canned mappings.
        from app.discovery.sources.hardcover import HardcoverSource

        canned = {
            "books": [
                {
                    "id": 100,
                    "book_mappings": [
                        {"external_id": "gr-100", "platform": {"name": "Goodreads"}},
                        {"external_id": "/books/OL100M", "platform": {"name": "OpenLibrary"}},
                        {"external_id": "gb-100", "platform": {"name": "Google"}},
                    ],
                },
                {
                    "id": 200,
                    "book_mappings": [
                        {"external_id": "gr-200", "platform": {"name": "Goodreads"}},
                    ],
                },
            ]
        }

        async def fake_query(self, query: str, variables=None):
            return canned

        monkeypatch.setattr(HardcoverSource, "_query", fake_query)

        stats = hygiene._zero_stats()
        await hygiene.job_hardcover_id_backfill("testlib", stats)

        row_a = await (await db.execute(
            "SELECT goodreads_id, openlibrary_id, google_books_id "
            "FROM books WHERE id = ?", (book_a,),
        )).fetchone()
        assert row_a["goodreads_id"] == "gr-100"
        # `/books/` prefix stripped (matches the live Hardcover path
        # form the v2.16.0 Gap 1 fix probes).
        assert row_a["openlibrary_id"] == "OL100M"
        assert row_a["google_books_id"] == "gb-100"

        # Book B's existing goodreads_id must NOT have been
        # overwritten — COALESCE-fill is the universal rule.
        row_b = await (await db.execute(
            "SELECT goodreads_id FROM books WHERE id = ?", (book_b,),
        )).fetchone()
        assert row_b["goodreads_id"] == "existing-gr-id"
        assert stats["books_backfilled"] >= 1


# ─── Job 4 — Identifier-keyed dedup ─────────────────────────────────


class TestIdentifierDedup:
    async def test_merges_two_books_sharing_goodreads_id(self, hygiene_dbs):
        db = hygiene_dbs
        a = await _insert_author(db, "X")
        # Winner is the lowest id row with owned=1.
        winner = await _insert_book(
            db, author_id=a, title="Winner",
            goodreads_id="42", owned=1, isbn=None,
        )
        # Loser has a non-null ISBN the winner is missing — the
        # COALESCE-fill should carry it onto the winner.
        loser = await _insert_book(
            db, author_id=a, title="Loser",
            goodreads_id="42", owned=0,
            isbn="9999",
        )

        stats = hygiene._zero_stats()
        merged = await hygiene._dedupe_by_identifier(
            db, "goodreads_id", stats, "testlib",
        )
        assert merged == 1
        # Winner survives, loser is gone.
        winner_row = await (await db.execute(
            "SELECT id, isbn FROM books WHERE id = ?", (winner,),
        )).fetchone()
        assert winner_row is not None
        assert winner_row["isbn"] == "9999"  # COALESCE-filled from loser
        gone = await (await db.execute(
            "SELECT id FROM books WHERE id = ?", (loser,),
        )).fetchone()
        assert gone is None

    async def test_skips_hidden_books(self, hygiene_dbs):
        """A hidden row sharing an identifier with an active row must
        NOT be merged — the user hid it intentionally."""
        db = hygiene_dbs
        a = await _insert_author(db, "X")
        active = await _insert_book(
            db, author_id=a, title="Active",
            goodreads_id="100", owned=1, hidden=0,
        )
        hidden = await _insert_book(
            db, author_id=a, title="Hidden",
            goodreads_id="100", owned=0, hidden=1,
        )

        stats = hygiene._zero_stats()
        merged = await hygiene._dedupe_by_identifier(
            db, "goodreads_id", stats, "testlib",
        )
        assert merged == 0
        # Both rows still present.
        rows = await (await db.execute(
            "SELECT id FROM books WHERE goodreads_id='100'"
        )).fetchall()
        ids = {r["id"] for r in rows}
        assert active in ids
        assert hidden in ids


# ─── Coordinator — empty-library no-op ──────────────────────────────


class TestRunAllEmptyLibrary:
    async def test_chain_completes_on_empty_db(self, hygiene_dbs):
        """A fresh DB with no books / authors should run all 6 jobs
        without errors and return zero-touch counters."""
        result = await hygiene.run_all()
        assert result["errors"] == []
        assert result["deleted_authors"] == 0
        assert result["books_backfilled"] == 0
        assert result["books_merged"] == 0
        assert result["series_merged"] == 0
        # All 6 jobs landed in the progress.jobs list.
        assert len(state._hygiene_progress["jobs"]) == hygiene.TOTAL_JOBS
