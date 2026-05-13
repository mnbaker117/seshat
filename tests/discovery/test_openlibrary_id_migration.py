"""
Tests for the v2.10.9 openlibrary_id column migration.

The discovery merge layer (`app/discovery/lookup.py`) uses
`f"{source_name}_id"` to dynamically build the column name when
writing source-specific external IDs to authors / series / books
rows. Open Library was added as a discovery source in v2.10.6 +
backfilled into upgraded installs in v2.10.8, but the corresponding
`openlibrary_id` columns were never added to any of the three tables.
Result: every Open Library merge raised `no such column:
openlibrary_id` and dropped the entire scan's contribution silently
(192 books for Sanderson in the v2.10.8 UAT).

These tests pin the v2.10.9 fix:
  - Columns present in the fresh-install SCHEMA
  - Migration adds them to existing installs upgrading from any
    pre-v2.10.9 release
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def fresh_db(tmp_path):
    """Build a sync sqlite DB by running the SCHEMA from scratch."""
    from app.discovery.database import SCHEMA
    db_path = tmp_path / "fresh.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def legacy_db(tmp_path):
    """Build a sync sqlite DB shaped like a v2.10.8 install — has
    every column EXCEPT openlibrary_id on the three relevant tables.
    Then run the migration list and confirm openlibrary_id appears."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    # Minimal v2.10.8-era schema — just the three tables we care about,
    # without openlibrary_id.
    conn.executescript("""
        CREATE TABLE authors (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            hardcover_id TEXT,
            goodreads_id TEXT,
            kobo_id TEXT
        );
        CREATE TABLE series (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            hardcover_id TEXT
        );
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            hardcover_id TEXT,
            goodreads_id TEXT
        );
    """)
    conn.commit()
    yield conn
    conn.close()


def _column_names(conn, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


class TestFreshInstallSchema:
    """v2.10.9 — every fresh install ships with openlibrary_id columns
    on authors/series/books so the discovery merge never crashes."""

    def test_authors_has_openlibrary_id(self, fresh_db):
        assert "openlibrary_id" in _column_names(fresh_db, "authors")

    def test_series_has_openlibrary_id(self, fresh_db):
        assert "openlibrary_id" in _column_names(fresh_db, "series")

    def test_books_has_openlibrary_id(self, fresh_db):
        assert "openlibrary_id" in _column_names(fresh_db, "books")


class TestMigrationOnExistingInstall:
    """v2.10.9 migration — replays the ALTER TABLE statements at the
    end of MIGRATIONS against a v2.10.8-shaped DB and verifies
    openlibrary_id columns appear on all three tables."""

    def test_migration_adds_openlibrary_id_columns(self, legacy_db):
        from app.discovery.database import MIGRATIONS

        # Run only the v2.10.9 ALTER statements (the openlibrary_id
        # adds at the end of MIGRATIONS). We avoid running the whole
        # MIGRATIONS list because most of those statements assume
        # tables/columns the test fixture doesn't have. The pattern
        # we care about is: each is an "ALTER TABLE … ADD COLUMN
        # openlibrary_id TEXT" idempotently runnable.
        ol_statements = [m for m in MIGRATIONS if "openlibrary_id" in m]
        assert len(ol_statements) == 3, (
            "v2.10.9 should add openlibrary_id to authors, series, books"
        )

        for stmt in ol_statements:
            legacy_db.execute(stmt)
        legacy_db.commit()

        assert "openlibrary_id" in _column_names(legacy_db, "authors")
        assert "openlibrary_id" in _column_names(legacy_db, "series")
        assert "openlibrary_id" in _column_names(legacy_db, "books")

    def test_inserting_openlibrary_id_works(self, legacy_db):
        """Smoke test: after migration, a discovery merge that uses
        the dynamic `f"{source_name}_id"` pattern can actually write
        an Open Library work key without raising."""
        from app.discovery.database import MIGRATIONS

        for stmt in [m for m in MIGRATIONS if "openlibrary_id" in m]:
            legacy_db.execute(stmt)
        legacy_db.commit()

        # Mirror lookup.py's UPDATE pattern with source_name="openlibrary"
        legacy_db.execute(
            "INSERT INTO authors (name, openlibrary_id) VALUES (?, ?)",
            ("Brandon Sanderson", "OL1394865A"),
        )
        legacy_db.execute(
            "INSERT INTO books (title, openlibrary_id) VALUES (?, ?)",
            ("The Way of Kings", "OL15161W"),
        )
        legacy_db.commit()

        row = legacy_db.execute(
            "SELECT openlibrary_id FROM authors WHERE name = ?",
            ("Brandon Sanderson",),
        ).fetchone()
        assert row[0] == "OL1394865A"
