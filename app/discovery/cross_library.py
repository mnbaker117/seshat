"""
Cross-library query aggregation.

Each discovered library has its own `seshat_{slug}.db`. Most discovery
endpoints read from the "active" library, which is fine for single-
library use but doesn't support "show me every audiobook regardless of
which library it's in".

This module provides helpers that:

  1. Enumerate libraries by `content_type` ("ebook" / "audiobook" /
     "all") from `state._discovered_libraries`.
  2. Open each library's DB, run a caller-supplied query function
     against it, and tag the returned rows with `library_slug` +
     `content_type`.
  3. Aggregate, sort, paginate in Python.

Pagination strategy: pull ALL matching rows from every library, sort
the full set, slice. O(N·K) where N is total books and K is the
number of libraries. Works up through the tens-of-thousands range
typical of a personal library; we can move to per-library windowing
if someone ever hits a scale where that matters.

The per-library DB connections are opened / closed per call — matches
the existing `get_db` pattern and avoids stale handles across
library sync events. Negligible compared to the HTTP overhead.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

import aiosqlite

from app import state
from app.discovery.database import get_db as get_library_db

_log = logging.getLogger("seshat.discovery.cross_library")


def libraries_for(content_type: Optional[str]) -> list[dict]:
    """Return the library dicts whose content_type matches the filter.

    "ebook" / "audiobook" — narrow to that content type.
    "all" or None / empty — return every discovered library.

    Empty result when the user has no libraries of the requested type
    (e.g. ABS-only user picks "ebook" — they just see an empty list,
    not an error).
    """
    if not content_type or content_type == "all":
        return list(state._discovered_libraries)
    return [
        lib for lib in state._discovered_libraries
        if (lib.get("content_type") or "ebook") == content_type
    ]


async def run_across_libraries(
    content_type: Optional[str],
    query: Callable[[aiosqlite.Connection], Awaitable[list[dict]]],
) -> list[dict]:
    """Run `query(db)` against every library of the given content type.

    Each returned row is stamped with `library_slug` + `content_type`
    (from the library config, not the row itself) so callers can
    render per-book library/format badges without another round-trip.

    Errors in a single library log a warning and leave the rest of
    the aggregation intact — a broken ABS library shouldn't 500 the
    ebook view, and vice versa.
    """
    libs = libraries_for(content_type)
    out: list[dict] = []
    for lib in libs:
        slug = lib.get("slug")
        if not slug:
            continue
        try:
            db = await get_library_db(slug)
        except Exception as e:
            _log.warning("cross-library open failed for %s: %s", slug, e)
            continue
        try:
            rows = await query(db)
        except Exception as e:
            _log.warning(
                "cross-library query failed for %s: %s", slug, e,
            )
            rows = []
        finally:
            await db.close()
        lib_content_type = lib.get("content_type") or "ebook"
        for r in rows:
            r["library_slug"] = slug
            r["library_name"] = lib.get("name") or slug
            # Preserve any row-level content_type if set (future-proof
            # for mixed libraries), else tag with the library's type.
            r.setdefault("content_type", lib_content_type)
        out.extend(rows)
    return out


def sort_and_paginate(
    rows: list[dict],
    *,
    sort_key: Callable[[dict], Any],
    reverse: bool,
    page: int,
    per_page: int,
) -> tuple[list[dict], int]:
    """Stable-sort `rows` and return (window, total_count).

    Stable ordering matters when aggregating: two libraries that each
    contain a book with the same sort key should hand out the same
    relative order every request so pagination doesn't jitter. Python's
    `sorted` is stable; we just have to make sure `sort_key` is
    deterministic.
    """
    total = len(rows)
    if total == 0:
        return [], 0
    rows_sorted = sorted(rows, key=sort_key, reverse=reverse)
    start = (page - 1) * per_page
    end = start + per_page
    return rows_sorted[start:end], total


SORT_KEYS: dict[str, Callable[[dict], Any]] = {
    # Case-folded so "alice" and "Alice" sort together across libraries
    # whose sort_name casing might drift. None-safety: fall back to ""
    # so a row with NULL title doesn't blow the comparison.
    "title": lambda r: ((r.get("title") or "").lower(),),
    "author": lambda r: (
        (r.get("author_sort_name") or r.get("author_name") or "").lower(),
        (r.get("title") or "").lower(),
    ),
    # v2.11.1 N2: NULL-series rows sort to the END on ASC. First
    # tuple slot is 0 for has-series, 1 for no-series — so
    # has-series rows come first ascending. On DESC the reverse=True
    # in `_sorted_page` flips everything, so no-series moves to the
    # front (standard SQL `ORDER BY series_name DESC NULLS LAST`-vs-
    # `NULLS FIRST` choice). Acceptable: the typical use case is
    # asc-by-series with no-series rows pushed to the end.
    "series": lambda r: (
        0 if r.get("series_name") else 1,
        (r.get("series_name") or "").lower(),
        float(r.get("series_index") or 0.0),
        (r.get("title") or "").lower(),
    ),
    "date": lambda r: (r.get("pub_date") or "",),
    "added": lambda r: (r.get("first_seen_at") or 0.0,),
    "name": lambda r: (
        (r.get("sort_name") or r.get("name") or "").lower(),
    ),
}


def sort_key_for(field: str) -> Callable[[dict], Any]:
    """Lookup a pre-declared sort key, default to title."""
    return SORT_KEYS.get(field, SORT_KEYS["title"])
