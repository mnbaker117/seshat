"""
Tests for calibre_sync's author-scoped series lookup.

Calibre can hold two unrelated authors who happen to share a series
name (Cressman/Savarovsky "The Last Paladin"). The series lookup
during sync used to be a global LOWER(name) match, which collapsed
both authors' books onto a single Seshat series row. It is now
author-scoped, mirroring the v2.2.7 fix in `_ensure_series_for_author`.
"""
from __future__ import annotations

import pytest


def _book(book_id, title, author_name, author_id, series_name=None,
          series_id=None, series_index=1.0):
    return {
        "book_id": book_id,
        "title": title,
        "pubdate": "2024-01-01",
        "series_index": series_index,
        "book_path": f"{author_name}/{title}",
        "cover_path": None,
        "isbn": None,
        "authors": [{
            "id": author_id,
            "name": author_name,
            "sort": author_name,
        }],
        "series": (
            [{"id": series_id, "name": series_name}]
            if series_name else []
        ),
        "tags": None,
        "rating": None,
        "description": None,
        "language": None,
        "publisher": None,
        "formats": None,
    }


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


async def _series_rows():
    from app.discovery.database import get_db
    db = await get_db()
    try:
        rows = await (await db.execute(
            "SELECT id, name, author_id FROM series ORDER BY id"
        )).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def _book_rows():
    from app.discovery.database import get_db
    db = await get_db()
    try:
        rows = await (await db.execute(
            "SELECT title, author_id, series_id FROM books ORDER BY title"
        )).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def test_same_series_name_different_authors_get_separate_rows(
    discovery_db, monkeypatch,
):
    """The Cressman/Savarovsky case: two authors each with a Calibre
    series named "The Last Paladin" must produce two Seshat series
    rows, not one shared row."""
    from app.discovery import calibre_sync

    monkeypatch.setattr(
        calibre_sync, "_read_calibre_db",
        lambda *a, **kw: {"books": [
            _book(1, "The Last Paladin",
                  "John Cressman", author_id=580,
                  series_name="The Last Paladin", series_id=100,
                  series_index=1.0),
            _book(2, "The Last Paladin #1",
                  "Roman Savarovsky", author_id=549,
                  series_name="The Last Paladin", series_id=200,
                  series_index=1.0),
        ]},
    )
    await calibre_sync.sync_calibre("x", "y")

    series = await _series_rows()
    paladin_rows = [s for s in series if s["name"] == "The Last Paladin"]
    assert len(paladin_rows) == 2
    assert {r["author_id"] for r in paladin_rows} == \
        {s["author_id"] for s in series if s["name"] == "The Last Paladin"}
    # Each author got their own row.
    author_to_series = {r["author_id"]: r["id"] for r in paladin_rows}
    assert len(author_to_series) == 2

    books = await _book_rows()
    assert len(books) == 2
    # Books point to series rows owned by their own author.
    for b in books:
        assert b["series_id"] == author_to_series[b["author_id"]]


async def test_same_author_same_series_name_dedupes(
    discovery_db, monkeypatch,
):
    """Sanity: a single author with multiple Calibre books in the
    same series still produces ONE Seshat series row."""
    from app.discovery import calibre_sync

    monkeypatch.setattr(
        calibre_sync, "_read_calibre_db",
        lambda *a, **kw: {"books": [
            _book(1, "Book One", "Solo Author", author_id=900,
                  series_name="My Series", series_id=50, series_index=1.0),
            _book(2, "Book Two", "Solo Author", author_id=900,
                  series_name="My Series", series_id=50, series_index=2.0),
        ]},
    )
    await calibre_sync.sync_calibre("x", "y")

    series = await _series_rows()
    my_series = [s for s in series if s["name"] == "My Series"]
    assert len(my_series) == 1
