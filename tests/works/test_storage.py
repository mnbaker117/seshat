"""
CRUD tests for the `work_links` table.

`temp_db` (in tests/conftest.py) monkeypatches `APP_DB_PATH` and runs
`init_db()` so the test gets an empty pipeline DB with the Phase 5
schema already applied. All storage helpers open their own connection
via `get_db()`, which returns the patched path.
"""
from __future__ import annotations

import pytest

from app.works import storage


async def test_create_and_get_link(temp_db):
    wid = storage.generate_work_id()
    await storage.create_link(
        work_id=wid,
        library_slug="calibre",
        book_id=1,
        content_type="ebook",
    )
    link = await storage.get_link("calibre", 1)
    assert link is not None
    assert link.work_id == wid
    assert link.content_type == "ebook"
    assert link.link_source == "auto"


async def test_duplicate_insert_ignored(temp_db):
    wid = storage.generate_work_id()
    await storage.create_link(
        work_id=wid, library_slug="calibre", book_id=1, content_type="ebook",
    )
    # Second call with a different work_id should be IGNORE'd (the
    # unique constraint is on (library_slug, book_id)).
    await storage.create_link(
        work_id="different-id", library_slug="calibre", book_id=1,
        content_type="ebook",
    )
    link = await storage.get_link("calibre", 1)
    assert link.work_id == wid  # original preserved


async def test_get_work_members(temp_db):
    wid = storage.generate_work_id()
    await storage.merge_books_into_work(
        work_id=wid,
        members=[
            {"library_slug": "calibre", "book_id": 1, "content_type": "ebook"},
            {"library_slug": "abs", "book_id": 10, "content_type": "audiobook"},
        ],
    )
    members = await storage.get_work_members(wid)
    assert len(members) == 2
    slugs = {m.library_slug for m in members}
    assert slugs == {"calibre", "abs"}


async def test_merge_counts_only_inserts(temp_db):
    """A duplicate in `members` should not be counted as an insert."""
    wid = storage.generate_work_id()
    inserted = await storage.merge_books_into_work(
        work_id=wid,
        members=[
            {"library_slug": "calibre", "book_id": 1, "content_type": "ebook"},
            {"library_slug": "calibre", "book_id": 1, "content_type": "ebook"},
        ],
    )
    assert inserted == 1


async def test_unlink_book(temp_db):
    wid = storage.generate_work_id()
    await storage.create_link(
        work_id=wid, library_slug="calibre", book_id=1, content_type="ebook",
    )
    assert await storage.unlink_book("calibre", 1) is True
    assert await storage.get_link("calibre", 1) is None
    # Second unlink is a no-op.
    assert await storage.unlink_book("calibre", 1) is False


async def test_list_works_filters_by_library(temp_db):
    w1 = storage.generate_work_id()
    w2 = storage.generate_work_id()
    await storage.create_link(
        work_id=w1, library_slug="calibre", book_id=1, content_type="ebook",
    )
    await storage.create_link(
        work_id=w2, library_slug="abs", book_id=20, content_type="audiobook",
    )
    all_works = await storage.list_works()
    assert set(all_works) == {w1, w2}
    calibre_only = await storage.list_works(library_slug="calibre")
    assert calibre_only == [w1]
    abs_only = await storage.list_works(content_type="audiobook")
    assert abs_only == [w2]


async def test_delete_work_removes_all_members(temp_db):
    wid = storage.generate_work_id()
    await storage.merge_books_into_work(
        work_id=wid,
        members=[
            {"library_slug": "calibre", "book_id": 1, "content_type": "ebook"},
            {"library_slug": "abs", "book_id": 10, "content_type": "audiobook"},
        ],
    )
    removed = await storage.delete_work(wid)
    assert removed == 2
    assert await storage.list_works() == []


async def test_reconcile_prunes_missing_books(temp_db):
    """Books no longer in their source library drop out."""
    wid = storage.generate_work_id()
    await storage.merge_books_into_work(
        work_id=wid,
        members=[
            {"library_slug": "calibre", "book_id": 1, "content_type": "ebook"},
            {"library_slug": "calibre", "book_id": 2, "content_type": "ebook"},
            {"library_slug": "calibre", "book_id": 3, "content_type": "ebook"},
        ],
    )
    # Book 2 disappeared from Calibre.
    removed = await storage.reconcile_library("calibre", [1, 3])
    assert removed == 1
    remaining = [m.book_id for m in await storage.get_work_members(wid)]
    assert set(remaining) == {1, 3}


async def test_reconcile_empty_list_is_safe(temp_db):
    """Empty `live_book_ids` is treated as a transient read error — no-op."""
    wid = storage.generate_work_id()
    await storage.create_link(
        work_id=wid, library_slug="calibre", book_id=1, content_type="ebook",
    )
    removed = await storage.reconcile_library("calibre", [])
    assert removed == 0
    assert await storage.get_link("calibre", 1) is not None
