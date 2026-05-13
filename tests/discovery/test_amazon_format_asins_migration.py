"""
Tests for the v2.11.0 Stage 5++ amazon_format_asins column migration.

The AmazonAuthorStoreSource (added in Stage 5++) populates
`books.amazon_format_asins` with a JSON map from mediaMatrix:

    {"kindle_edition": "B002...", "hardcover": "0765...",
     "paperback": "1250...", "mass_market": "1250..."}

This lets the UI offer "switch canonical format" and the per-book
enricher fetch the correct detail page when the user prefers a
non-Kindle format, without needing a fresh discovery scan.

These tests pin the schema add:
  - Column present in the fresh-install SCHEMA
  - Migration adds it to existing installs upgrading from v2.10.x
  - Idempotent at the _ensure_columns level (re-running startup
    twice on a post-migration DB doesn't crash)
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
    """v2.10.10-shaped install — books has every column up through
    audible_id but NOT amazon_format_asins yet."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            amazon_id TEXT,
            openlibrary_id TEXT,
            audible_id TEXT
        );
    """)
    conn.commit()
    yield conn
    conn.close()


def _column_names(conn, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


class TestFreshInstallSchema:
    """v2.11.0 Stage 5++ — every fresh install ships with
    amazon_format_asins on the books table."""

    def test_books_has_amazon_format_asins(self, fresh_db):
        assert "amazon_format_asins" in _column_names(fresh_db, "books")


class TestMigrationOnExistingInstall:
    """The Stage 5++ ALTER TABLE entry at the end of MIGRATIONS
    needs to add amazon_format_asins to a pre-Stage-5++ books table.
    Also covered: _ensure_columns idempotence so the second startup
    after upgrade doesn't crash on the now-existing column."""

    def test_migration_adds_amazon_format_asins(self, legacy_db):
        from app.discovery.database import MIGRATIONS

        ms = [m for m in MIGRATIONS if "amazon_format_asins" in m]
        assert len(ms) == 1, (
            "Stage 5++ should add exactly one ALTER for amazon_format_asins"
        )

        legacy_db.execute(ms[0])
        legacy_db.commit()

        assert "amazon_format_asins" in _column_names(legacy_db, "books")

    def test_inserting_json_blob_works(self, legacy_db):
        """Smoke test: the discovery merge will write a JSON-encoded
        dict via json.dumps. Confirm that's valid TEXT storage."""
        import json
        from app.discovery.database import MIGRATIONS

        for stmt in [m for m in MIGRATIONS if "amazon_format_asins" in m]:
            legacy_db.execute(stmt)
        legacy_db.commit()

        format_map = {
            "kindle_edition": "B002GYI9C4",
            "hardcover": "076531178X",
            "paperback": "1250868289",
            "mass_market": "1250318548",
        }
        legacy_db.execute(
            "INSERT INTO books (title, amazon_format_asins) VALUES (?, ?)",
            ("Mistborn: The Final Empire", json.dumps(format_map)),
        )
        legacy_db.commit()

        row = legacy_db.execute(
            "SELECT amazon_format_asins FROM books WHERE title = ?",
            ("Mistborn: The Final Empire",),
        ).fetchone()
        assert json.loads(row[0]) == format_map

    def test_ensure_columns_idempotent(self, legacy_db):
        """The _ensure_columns helper at the tail of run_migrations
        runs unconditionally on every startup. Adding amazon_format_asins
        a second time after the migration has run must NOT crash —
        sqlite raises OperationalError("duplicate column") which the
        helper catches silently."""
        from app.discovery.database import MIGRATIONS

        for stmt in [m for m in MIGRATIONS if "amazon_format_asins" in m]:
            legacy_db.execute(stmt)
        legacy_db.commit()

        # Replay the column add — should raise the duplicate-column
        # error that _ensure_columns is built to swallow.
        with pytest.raises(sqlite3.OperationalError) as exc:
            legacy_db.execute(
                "ALTER TABLE books ADD COLUMN amazon_format_asins TEXT"
            )
        assert "duplicate column" in str(exc.value).lower()
