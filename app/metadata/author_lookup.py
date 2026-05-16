"""
Cross-library author lookup helpers for the metadata enricher.

The enricher runs library-agnostic — by the time `enrich()` is
called, the completed download hasn't yet been linked to a specific
library's `books` row (acquisition linkback happens later). So when
we want the author's stored `goodreads_id` to anchor GoodreadsSource's
T4/T5 resolver tiers, we have to walk every discovered library's
authors table and take the first non-empty match.

Author identity is global (the same person has the same Goodreads ID
in every library), so picking the first hit is correct. If a library
happens to hold a wrong ID, the enricher's downstream `score_match()`
gate will reject the resulting bogus MetaRecord on confidence anyway.

The input `name` is whatever the enricher held in `metadata.author` —
often a multi-author comma/and/&-joined blob like "Alex Toxic, Nadya
Lee". The `authors` table stores each name as its own row, so we
split the blob via `app.filter.gate.split_authors` and try each name
in primary-first order, returning the first hit. Primary-first
matters because the resolver's T4/T5 tiers fuzzy-match against the
anchor author's bibliography — co-author bibliographies are a less
reliable place to find a book where someone else is the headline
author.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("seshat.metadata.author_lookup")


async def get_goodreads_id_for_author(name: str) -> str:
    """Return the stored `authors.goodreads_id` for `name`, or "".

    Splits multi-author blobs and walks every discovered library
    looking for any individual name (primary author tried first).
    Returns the first non-empty goodreads_id found. Empty string
    when no name matches any library, the matched author has no
    stored goodreads_id, or the discovery state hasn't initialized
    (test mode).
    """
    if not name or not name.strip():
        return ""

    # Defer state import so test code paths that don't need this
    # can avoid pulling the global library state.
    try:
        from app import state
        from app.discovery.database import get_db as get_library_db
        from app.filter.gate import split_authors
    except Exception:
        return ""

    libraries = list(state._discovered_libraries or [])
    if not libraries:
        return ""

    individual_names = split_authors(name)
    if not individual_names:
        return ""

    for individual_name in individual_names:
        target = individual_name.strip()
        if not target:
            continue
        for lib in libraries:
            slug = (lib or {}).get("slug")
            if not slug:
                continue
            try:
                db = await get_library_db(slug)
            except Exception:
                continue
            try:
                row = await (await db.execute(
                    "SELECT goodreads_id FROM authors WHERE name = ?",
                    (target,),
                )).fetchone()
                if row and row[0]:
                    return str(row[0])
            except Exception as e:
                _log.debug(
                    "author_lookup: %s author lookup failed for %r: %s",
                    slug, target, e,
                )
            finally:
                try:
                    await db.close()
                except Exception:
                    pass
    return ""
