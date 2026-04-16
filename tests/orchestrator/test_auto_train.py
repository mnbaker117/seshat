"""
Unit tests for the auto-train module.
"""
from app.database import get_db
from app.orchestrator.auto_train import train_author, train_authors_from_blob


class TestTrainAuthor:
    async def test_adds_new_author(self, temp_db):
        db = await get_db()
        try:
            result = await train_author(db, "Brandon Sanderson")
            assert result is True

            cursor = await db.execute(
                "SELECT name, normalized, source FROM authors_allowed"
            )
            row = await cursor.fetchone()
            assert row["name"] == "Brandon Sanderson"
            assert row["normalized"] == "brandon sanderson"
            assert row["source"] == "auto_train"
        finally:
            await db.close()

    async def test_skips_existing_author(self, temp_db):
        db = await get_db()
        try:
            await train_author(db, "Brandon Sanderson")
            result = await train_author(db, "Brandon Sanderson")
            assert result is False

            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM authors_allowed"
            )
            row = await cursor.fetchone()
            assert row["cnt"] == 1
        finally:
            await db.close()

    async def test_skips_ignored_author(self, temp_db):
        db = await get_db()
        try:
            # Add to ignore list first.
            await db.execute(
                "INSERT INTO authors_ignored (name, normalized, source) "
                "VALUES (?, ?, ?)",
                ("Stephen King", "stephen king", "manual"),
            )
            await db.commit()

            result = await train_author(db, "Stephen King")
            assert result is False

            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM authors_allowed"
            )
            row = await cursor.fetchone()
            assert row["cnt"] == 0
        finally:
            await db.close()

    async def test_empty_name_skipped(self, temp_db):
        db = await get_db()
        try:
            assert await train_author(db, "") is False
            assert await train_author(db, "   ") is False
        finally:
            await db.close()

    async def test_normalizes_calibre_sort_form(self, temp_db):
        db = await get_db()
        try:
            await train_author(db, "Brandon Sanderson")
            # "Sanderson, Brandon" should match via normalization.
            result = await train_author(db, "Sanderson, Brandon")
            assert result is False
        finally:
            await db.close()

    async def test_custom_source(self, temp_db):
        db = await get_db()
        try:
            await train_author(db, "Test Author", source="calibre_sync")
            cursor = await db.execute(
                "SELECT source FROM authors_allowed WHERE normalized = ?",
                ("test author",),
            )
            row = await cursor.fetchone()
            assert row["source"] == "calibre_sync"
        finally:
            await db.close()


class TestTrainAuthorsFromBlob:
    async def test_splits_and_trains(self, temp_db):
        db = await get_db()
        try:
            added = await train_authors_from_blob(
                db, "J N Chaney, Jason Anspach"
            )
            assert added == 2

            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM authors_allowed"
            )
            row = await cursor.fetchone()
            assert row["cnt"] == 2
        finally:
            await db.close()

    async def test_skips_already_trained(self, temp_db):
        db = await get_db()
        try:
            await train_authors_from_blob(db, "Author A, Author B")
            added = await train_authors_from_blob(db, "Author A, Author C")
            # Only Author C should be newly added.
            assert added == 1
        finally:
            await db.close()

    async def test_empty_blob(self, temp_db):
        db = await get_db()
        try:
            assert await train_authors_from_blob(db, "") == 0
        finally:
            await db.close()
