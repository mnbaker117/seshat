"""
Global search endpoint for the top-nav search bar (v2.15.0 #B).

Returns a small set of best-match books, authors, and series across
every discovered library for one user-supplied query string. The
frontend pairs the response with a client-side index of pages +
Settings field labels to render a unified categorized dropdown.

Scope notes:

  - Substring LIKE matching, case-insensitive. We're not trying to
    be a search engine — Mark's library is ~16k books + ~750
    authors + ~2.5k series, all of which fit in fast LIKE scans.
  - `limit` is per-category, defaults to 8. Tuned for the dropdown
    (around 8 items per group fits without scrolling on a typical
    viewport).
  - Cross-library: results from every library are merged, each row
    stamped with `library_slug` + `library_name` so the dropdown
    can show where the result lives + so click-through can deep-
    link to the right library.
"""
from __future__ import annotations

import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.discovery.cross_library import run_across_libraries

_log = logging.getLogger("seshat.routers.search")

router = APIRouter(prefix="/api/v1", tags=["search"])


class BookHit(BaseModel):
    id: int
    title: str
    author_name: Optional[str] = None
    author_id: Optional[int] = None
    series_name: Optional[str] = None
    library_slug: Optional[str] = None
    library_name: Optional[str] = None
    content_type: Optional[str] = None
    owned: Optional[int] = None


class AuthorHit(BaseModel):
    id: int
    name: str
    library_slug: Optional[str] = None
    library_name: Optional[str] = None
    content_type: Optional[str] = None
    book_count: Optional[int] = None


class SeriesHit(BaseModel):
    id: int
    name: str
    author_name: Optional[str] = None
    author_id: Optional[int] = None
    library_slug: Optional[str] = None
    library_name: Optional[str] = None
    content_type: Optional[str] = None


class SearchResponse(BaseModel):
    q: str
    books: list[BookHit]
    authors: list[AuthorHit]
    series: list[SeriesHit]


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(8, ge=1, le=50),
) -> SearchResponse:
    """Global search across books, authors, and series.

    `q` is case-insensitive substring match. Empty / short queries
    are rejected at the validation layer (min_length=1); the
    frontend gates on >=2 chars before calling so the API isn't
    pinged on every single keystroke.
    """
    needle = f"%{q}%"

    async def search_books(db: aiosqlite.Connection) -> list[dict]:
        # Owned books first (the user's own library is what they
        # mean 90% of the time when they type a title). Within each
        # owned-bucket, title-prefix matches rank above mid-string
        # matches — typing "wolf" surfaces "Wolf Tracks" before
        # "Lone Wolf in the Snow".
        cur = await db.execute(
            """
            SELECT b.id, b.title, b.author_id, b.owned, b.series_id,
                   a.name AS author_name,
                   s.name AS series_name
            FROM books b
            JOIN authors a ON b.author_id = a.id
            LEFT JOIN series s ON b.series_id = s.id
            WHERE b.title LIKE ? COLLATE NOCASE
              AND b.hidden = 0
            ORDER BY
              b.owned DESC,
              CASE WHEN b.title LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END,
              b.title COLLATE NOCASE
            LIMIT ?
            """,
            (needle, f"{q}%", limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def search_authors(db: aiosqlite.Connection) -> list[dict]:
        cur = await db.execute(
            """
            SELECT a.id, a.name,
                   COUNT(b.id) AS book_count
            FROM authors a
            LEFT JOIN books b ON b.author_id = a.id AND b.hidden = 0
            WHERE a.name LIKE ? COLLATE NOCASE
            GROUP BY a.id
            ORDER BY
              CASE WHEN a.name LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END,
              a.sort_name COLLATE NOCASE
            LIMIT ?
            """,
            (needle, f"{q}%", limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def search_series(db: aiosqlite.Connection) -> list[dict]:
        cur = await db.execute(
            """
            SELECT s.id, s.name, s.author_id,
                   a.name AS author_name
            FROM series s
            LEFT JOIN authors a ON s.author_id = a.id
            WHERE s.name LIKE ? COLLATE NOCASE
            ORDER BY
              CASE WHEN s.name LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END,
              s.name COLLATE NOCASE
            LIMIT ?
            """,
            (needle, f"{q}%", limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # Fan out across every library. `run_across_libraries` stamps each
    # row with `library_slug` + `library_name` for the frontend.
    book_rows = await run_across_libraries("all", search_books)
    author_rows = await run_across_libraries("all", search_authors)
    series_rows = await run_across_libraries("all", search_series)

    # Trim cross-library aggregation back down to `limit` total per
    # category. Per-library queries each return up to `limit`; on a
    # two-library install we'd otherwise return 2*limit hits. Owned
    # rows still rank first because each library sorted them that
    # way and Python's sort is stable.
    book_rows = book_rows[:limit]
    author_rows = author_rows[:limit]
    series_rows = series_rows[:limit]

    return SearchResponse(
        q=q,
        books=[BookHit(**r) for r in book_rows],
        authors=[AuthorHit(**r) for r in author_rows],
        series=[SeriesHit(**r) for r in series_rows],
    )
