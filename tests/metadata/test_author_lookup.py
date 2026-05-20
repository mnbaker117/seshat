"""
Tests for `app.metadata.author_lookup.get_goodreads_id_for_author`.

The function feeds GoodreadsSource's T4/T5 resolver tiers — an
empty return collapses both tiers to no_result on obscure books
with no ISBN/ASIN seed.

v2.17.7 — the prior implementation matched `authors.name` exactly,
which missed the "St Arkham" (MAM-side) vs "St. Arkham" (Calibre-
side) case. The fix routes the lookup through `normalized_name`
using the same `normalize_author_name` helper used at write time.
"""
from __future__ import annotations

import pytest

from app import state
from app.discovery import database as disco_db
from app.metadata.author_lookup import get_goodreads_id_for_author


@pytest.fixture
async def two_libraries(tmp_path, monkeypatch, temp_db):
    from app import config as app_config

    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(disco_db, "DATA_DIR", tmp_path)
    await disco_db.init_db("calibre-library")
    await disco_db.init_db("abs-audio-library")

    libs = [
        {"slug": "calibre-library", "content_type": "ebook",
         "app_type": "calibre"},
        {"slug": "abs-audio-library", "content_type": "audiobook",
         "app_type": "audiobookshelf"},
    ]
    monkeypatch.setattr(state, "_discovered_libraries", libs)
    yield libs


async def _seed_author(slug: str, name: str, goodreads_id: str) -> None:
    from app.metadata.author_names import normalize_author_name

    db = await disco_db.get_db(slug)
    try:
        await db.execute(
            "INSERT INTO authors (name, sort_name, normalized_name, "
            "                     goodreads_id) "
            "VALUES (?, ?, ?, ?)",
            (name, name, normalize_author_name(name), goodreads_id),
        )
        await db.commit()
    finally:
        await db.close()


async def test_punctuation_difference_still_matches(two_libraries):
    """Mark's St. Arkham case — MAM announce has 'St Arkham' (no
    period), Calibre stores 'St. Arkham'. The lookup must collapse
    the two via normalized_name."""
    await _seed_author("calibre-library", "St. Arkham", "62231932")
    gid = await get_goodreads_id_for_author("St Arkham")
    assert gid == "62231932"


async def test_initials_collapse(two_libraries):
    """`A K Duboff` and `A.K. Duboff` should resolve to the same
    `ak duboff` normalized form."""
    await _seed_author("calibre-library", "A K Duboff", "1136")
    gid = await get_goodreads_id_for_author("A.K. Duboff")
    assert gid == "1136"


async def test_no_match_returns_empty(two_libraries):
    gid = await get_goodreads_id_for_author("Unknown Author")
    assert gid == ""


async def test_empty_name_returns_empty(two_libraries):
    assert await get_goodreads_id_for_author("") == ""
    assert await get_goodreads_id_for_author("   ") == ""


async def test_multi_author_first_hit_wins(two_libraries):
    """Author blob like 'Author A, Author B' splits and tries primary
    first. If only the co-author has an ID stored, the helper still
    returns it."""
    await _seed_author("calibre-library", "Author B", "999")
    gid = await get_goodreads_id_for_author("Author A, Author B")
    assert gid == "999"


async def test_finds_in_either_library(two_libraries):
    # ABS-only author. The helper walks every library, not just
    # the first.
    await _seed_author("abs-audio-library", "X. Author", "12345")
    gid = await get_goodreads_id_for_author("X Author")
    assert gid == "12345"
