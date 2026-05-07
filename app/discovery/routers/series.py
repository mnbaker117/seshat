"""
Series endpoints — list, detail, and v2.3 Series Manager mutations.

  GET    /api/series                — every series the user has at
                                      least one visible book for, with
                                      owned/missing counts and
                                      multi-author flag
  GET    /api/series/{sid}          — full series detail with the
                                      ordered book list and per-book
                                      ownership state
  POST   /api/series/promote        — merge 2+ per-author rows into a
                                      single shared row (author_id=NULL)
  POST   /api/series/{sid}/demote   — split a shared row into per-author
                                      rows; books re-link by primary
                                      author
  PATCH  /api/series/{sid}          — rename a series
  DELETE /api/series/{sid}          — delete; books fall back to
                                      standalone (series_id=NULL)
  POST   /api/series/{sid}/books    — bulk-add books to this series
  DELETE /api/series/{sid}/books/{book_id} — detach a single book

The mutation endpoints are the v2.3 Series Manager backend. They
exist in addition to (not in place of) the auto-detect path in
calibre_sync.py — which handles the common case (Calibre-organized
shared series like Halo) without user intervention. The mutations
cover edge cases: source-discovered books that aren't yet in
Calibre, manual relabeling, undoing an auto-decision the user
disagreed with.

Both list/detail endpoints honor the global hidden-book filter so
the totals shown in the UI match what the user actually sees on
book pages.
"""
import logging
from fastapi import APIRouter, Body, HTTPException, Query

from app.discovery.database import get_db, HF

logger = logging.getLogger("seshat.discovery")

router = APIRouter(prefix="/api/discovery", tags=["series"])


@router.get("/series/{sid}")
async def get_series(sid: int, slug: str | None = None):
    """Return a series detail with its ordered book list.

    `slug=X` overrides which library's DB holds this series. Without
    it we use the active library. Needed for the cross-library author
    detail page: series ids from ABS don't mean anything in Calibre,
    so fetching books for an ABS-sourced series must go straight to
    the ABS DB. Same failure mode as the authors endpoint before the
    slug fix — ABS series 2 could be a totally different series in
    Calibre with the same id.

    Every returned book row is stamped with `library_slug` so the
    frontend's `coverSrcFor` picks the per-library cover URL. Without
    it the Calibre cover endpoint was serving a completely unrelated
    book's cover for each ABS book id.
    """
    # Active library fallback resolved explicitly so we can stamp
    # library_slug on every book row even when the caller didn't pass
    # one (single-library installs still benefit from correct metadata).
    from app.discovery.database import get_active_library as _get_active
    effective_slug = slug or _get_active() or ""
    db = await get_db(slug)
    try:
        r = await (await db.execute("SELECT s.*, a.name as author_name FROM series s LEFT JOIN authors a ON s.author_id=a.id WHERE s.id=?", (sid,))).fetchone()
        if not r:
            raise HTTPException(404)
        s = dict(r)
        # Pre-aggregated series_total via LEFT JOIN (same refactor as
        # routers/books.py) — avoids a correlated COUNT firing per returned
        # row. For this endpoint all returned rows share the same
        # series_id (the query is WHERE b.series_id=?), so every row's
        # series_total is identical — the old code computed it N times.
        # Content type looked up once from the library config — used
        # to stamp each row alongside library_slug so the frontend can
        # render audiobook badges and route cover requests properly.
        from app import state
        content_type = next(
            (l.get("content_type", "ebook") for l in state._discovered_libraries
             if l.get("slug") == effective_slug),
            "ebook",
        )
        rows = [
            {**dict(b), "library_slug": effective_slug, "content_type": content_type}
            for b in await (await db.execute(f"""
                SELECT b.*, a.name as author_name, sr.name as series_name,
                    COALESCE(st.series_total, 0) as series_total,
                    COALESCE(st.mainline_total, 0) as mainline_total
                FROM books b
                JOIN authors a ON b.author_id=a.id
                LEFT JOIN series sr ON b.series_id=sr.id
                LEFT JOIN (
                    SELECT series_id,
                           COUNT(*) AS series_total,
                           SUM(CASE WHEN series_index IS NOT NULL
                                     AND series_index >= 1
                                     AND series_index = CAST(series_index AS INTEGER)
                                    THEN 1 ELSE 0 END) AS mainline_total
                    FROM books
                    WHERE hidden=0 AND series_id IS NOT NULL
                    GROUP BY series_id
                ) st ON st.series_id = b.series_id
                WHERE b.series_id=? AND {HF}
                ORDER BY COALESCE(b.series_index,999), b.pub_date ASC
            """, (sid,))).fetchall()
        ]
        s["books"] = await _stamp_work_siblings(rows, effective_slug)
        return s
    finally:
        await db.close()


async def _stamp_work_siblings(
    books: list[dict], slug: str,
) -> list[dict]:
    """Attach cross-format sibling info to each book in a list.

    Looks up the pipeline DB's `work_links` table in bulk and, for
    every book with a cross-library twin, sets `work_siblings` to a
    list of `{library_slug, book_id, content_type}` dicts (excluding
    self). Books without a work row or without cross-library twins
    come back unchanged. Empty slug or empty list short-circuit.
    """
    if not slug or not books:
        return books
    from app.works.storage import get_siblings_for_books
    ids = [int(b["id"]) for b in books if b.get("id") is not None]
    if not ids:
        return books
    sib_map = await get_siblings_for_books(slug, ids)
    for b in books:
        s = sib_map.get(int(b["id"]))
        if s:
            b["work_id"] = s[0].work_id
            b["work_siblings"] = [
                {"library_slug": w.library_slug, "book_id": w.book_id,
                 "content_type": w.content_type}
                for w in s
            ]
    return books


@router.get("/series")
async def list_series(search: str = Query(None), sort: str = Query("name"), sort_dir: str = Query("asc"), has_missing: bool = Query(None), shared: bool = Query(None)):
    """List series with author info, owned/missing counts, multi-author
    flag.

    `shared=true` filters to shared rows only (`series.author_id IS NULL`).
    `shared=false` filters to per-author rows. Omit to return both.
    The Series Manager page uses this to surface candidates for
    promote/demote actions.
    """
    db = await get_db()
    try:
        q = f"""SELECT s.*, a.name as author_name,
            COUNT(DISTINCT CASE WHEN {HF} THEN b.id END) as book_count,
            SUM(CASE WHEN b.owned=1 AND {HF} THEN 1 ELSE 0 END) as owned_count,
            SUM(CASE WHEN b.owned=0 AND {HF} THEN 1 ELSE 0 END) as missing_count,
            CASE WHEN COUNT(DISTINCT b.author_id) > 1 THEN 1 ELSE 0 END as multi_author,
            CASE WHEN s.author_id IS NULL THEN 1 ELSE 0 END as is_shared,
            COUNT(DISTINCT b.author_id) as contributor_count
            FROM series s LEFT JOIN authors a ON s.author_id=a.id LEFT JOIN books b ON s.id=b.series_id"""
        p = []
        c = []
        if search:
            c.append("(s.name LIKE ? OR a.name LIKE ?)")
            p.extend([f"%{search}%"] * 2)
        if shared is True:
            c.append("s.author_id IS NULL")
        elif shared is False:
            c.append("s.author_id IS NOT NULL")
        if c:
            q += " WHERE " + " AND ".join(c)
        q += " GROUP BY s.id"
        if has_missing:
            q += " HAVING missing_count > 0"
        d = "DESC" if sort_dir == "desc" else "ASC"
        q += {"missing": f" ORDER BY missing_count {d}", "author": f" ORDER BY a.sort_name {d}"}.get(sort, f" ORDER BY s.name {d}")
        return {"series": [dict(r) for r in await (await db.execute(q, p)).fetchall()]}
    finally:
        await db.close()


# ── v2.3 Series Manager mutations ────────────────────────────────────


async def _series_or_404(db, sid: int) -> dict:
    row = await (await db.execute(
        "SELECT id, name, author_id FROM series WHERE id = ?", (sid,)
    )).fetchone()
    if not row:
        raise HTTPException(404, f"series {sid} not found")
    return dict(row)


@router.post("/series/promote")
async def promote_series(payload: dict = Body(...)):
    """Promote 2+ per-author series rows into a single shared row.

    Request body:
      {
        "series_ids": [10, 11, 12, ...],   # required, at least 2
        "name": "Halo"                      # optional override; if
                                            # omitted, uses the name
                                            # from the first series_id
      }

    Behavior:
      1. All listed series IDs must currently exist and have
         author_id IS NOT NULL (already shared rows can't be promoted
         again).
      2. Pick (or accept) the canonical shared name.
      3. UPSERT the shared row keyed on (LOWER(name), author_id IS NULL).
         Re-uses an existing shared row by that name if one exists,
         otherwise INSERTs a fresh one.
      4. UPDATE every book pointing at any of the source rows to
         point at the shared row instead.
      5. DELETE the source rows.

    Idempotent on accidental re-runs: a second promote with the same
    series_ids 404s on the now-deleted rows. Wrap in a single
    transaction so partial failure doesn't leave a half-merged state.
    """
    series_ids = payload.get("series_ids") or []
    if not isinstance(series_ids, list) or len(series_ids) < 2:
        raise HTTPException(400, "series_ids must be a list of 2+ ids")

    db = await get_db()
    try:
        # Validate all rows + collect names. Reject if any is already
        # shared (author_id IS NULL) — the user should pick a different
        # action.
        ph = ",".join("?" * len(series_ids))
        rows = await (await db.execute(
            f"SELECT id, name, author_id FROM series WHERE id IN ({ph})",
            series_ids,
        )).fetchall()
        rows = [dict(r) for r in rows]
        if len(rows) != len(series_ids):
            found = {r["id"] for r in rows}
            missing = [sid for sid in series_ids if sid not in found]
            raise HTTPException(404, f"series not found: {missing}")
        already_shared = [r["id"] for r in rows if r["author_id"] is None]
        if already_shared:
            raise HTTPException(
                400,
                f"already-shared series cannot be promoted: {already_shared}",
            )

        canonical_name = (payload.get("name") or rows[0]["name"]).strip()
        if not canonical_name:
            raise HTTPException(400, "name must not be empty")

        # Find or create the shared row.
        shared_row = await (await db.execute(
            "SELECT id FROM series WHERE LOWER(name) = LOWER(?) "
            "AND author_id IS NULL",
            (canonical_name,),
        )).fetchone()
        if shared_row:
            shared_id = shared_row["id"]
        else:
            cur = await db.execute(
                "INSERT INTO series (name, author_id) VALUES (?, NULL)",
                (canonical_name,),
            )
            shared_id = cur.lastrowid

        # Re-link books from every source row to the shared row, then
        # delete the source rows. Skip the shared_id itself if it
        # somehow appeared in the input list.
        old_ids = [r["id"] for r in rows if r["id"] != shared_id]
        if not old_ids:
            await db.commit()
            return {"shared_id": shared_id, "promoted_from": [],
                    "books_moved": 0}
        ph_old = ",".join("?" * len(old_ids))
        cur = await db.execute(
            f"UPDATE books SET series_id = ? WHERE series_id IN ({ph_old})",
            (shared_id, *old_ids),
        )
        books_moved = cur.rowcount or 0
        await db.execute(
            f"DELETE FROM series WHERE id IN ({ph_old})", old_ids,
        )
        await db.commit()

        return {
            "shared_id": shared_id,
            "promoted_from": old_ids,
            "books_moved": books_moved,
        }
    finally:
        await db.close()


@router.post("/series/{sid}/demote")
async def demote_series(sid: int):
    """Split a shared series row into per-author rows.

    For each distinct author whose books currently point at this
    shared row:
      1. UPSERT a per-author row with the same name (matching the
         author-scoped lookup that lookup.py and calibre_sync use).
      2. UPDATE that author's books to point at the per-author row.
    Then DELETE the shared row.

    400 if the row isn't shared (author_id IS NOT NULL).
    400 if the shared row has no books — there's nothing to split,
    just call DELETE instead.
    """
    db = await get_db()
    try:
        row = await _series_or_404(db, sid)
        if row["author_id"] is not None:
            raise HTTPException(
                400, "series is not shared (author_id is not NULL)"
            )

        author_rows = await (await db.execute(
            "SELECT DISTINCT author_id FROM books "
            "WHERE series_id = ? AND author_id IS NOT NULL",
            (sid,),
        )).fetchall()
        author_ids = [r["author_id"] for r in author_rows]
        if not author_ids:
            raise HTTPException(
                400, "shared series has no books to split"
            )

        new_series_ids = []
        books_moved_total = 0
        for aid in author_ids:
            # Re-use an existing per-author row by name if one happens
            # to exist (it shouldn't normally, but be safe).
            existing = await (await db.execute(
                "SELECT id FROM series WHERE LOWER(name) = LOWER(?) "
                "AND author_id = ?",
                (row["name"], aid),
            )).fetchone()
            if existing:
                new_id = existing["id"]
            else:
                cur = await db.execute(
                    "INSERT INTO series (name, author_id) VALUES (?, ?)",
                    (row["name"], aid),
                )
                new_id = cur.lastrowid
            cur = await db.execute(
                "UPDATE books SET series_id = ? "
                "WHERE series_id = ? AND author_id = ?",
                (new_id, sid, aid),
            )
            books_moved_total += cur.rowcount or 0
            new_series_ids.append(new_id)

        await db.execute("DELETE FROM series WHERE id = ?", (sid,))
        await db.commit()

        return {
            "demoted_from": sid,
            "new_series_ids": new_series_ids,
            "books_moved": books_moved_total,
        }
    finally:
        await db.close()


@router.patch("/series/{sid}")
async def rename_series(sid: int, payload: dict = Body(...)):
    """Rename a series.

    Request body: {"name": "New Name"}

    Conflict behavior: if another series row already has the same
    (name, author_id) — including (name, NULL) for shared — return
    409 with the conflicting row's id so the caller can offer a
    "merge into existing" affordance instead of forcing a duplicate.
    """
    new_name = (payload.get("name") or "").strip()
    if not new_name:
        raise HTTPException(400, "name must not be empty")

    db = await get_db()
    try:
        row = await _series_or_404(db, sid)
        if new_name == row["name"]:
            return {"id": sid, "name": new_name, "noop": True}

        # Conflict check uses the same composite as the UNIQUE
        # constraint: (LOWER(name), author_id) where NULL is matched
        # explicitly via IS.
        if row["author_id"] is None:
            conflict_row = await (await db.execute(
                "SELECT id FROM series WHERE LOWER(name) = LOWER(?) "
                "AND author_id IS NULL AND id != ?",
                (new_name, sid),
            )).fetchone()
        else:
            conflict_row = await (await db.execute(
                "SELECT id FROM series WHERE LOWER(name) = LOWER(?) "
                "AND author_id = ? AND id != ?",
                (new_name, row["author_id"], sid),
            )).fetchone()
        if conflict_row:
            raise HTTPException(
                409,
                {"message": "another series row already uses this name",
                 "conflict_id": conflict_row["id"]},
            )

        await db.execute(
            "UPDATE series SET name = ? WHERE id = ?", (new_name, sid),
        )
        await db.commit()
        return {"id": sid, "name": new_name}
    finally:
        await db.close()


@router.delete("/series/{sid}")
async def delete_series(sid: int):
    """Delete a series row. Books pointing at it fall back to
    standalone (series_id=NULL, series_index=NULL).

    Use cases: a bogus series the auto-detect created from a parser
    bug, or cleaning up after a manual mistake. For the common case
    of "this series row is wrong, here's the correct one" prefer
    promote/demote/membership-edit instead.
    """
    db = await get_db()
    try:
        await _series_or_404(db, sid)
        cur = await db.execute(
            "UPDATE books SET series_id = NULL, series_index = NULL "
            "WHERE series_id = ?", (sid,),
        )
        books_orphaned = cur.rowcount or 0
        await db.execute("DELETE FROM series WHERE id = ?", (sid,))
        await db.commit()
        return {"deleted": sid, "books_orphaned": books_orphaned}
    finally:
        await db.close()


@router.post("/series/{sid}/books")
async def add_books_to_series(sid: int, payload: dict = Body(...)):
    """Bulk-add books to a series.

    Request body:
      {
        "book_ids": [1, 2, 3],
        "indices": {"1": 1.0, "2": 2.0}   # optional per-book indices,
                                           # keyed as string for JSON
      }

    Books not listed in `indices` keep their existing series_index
    (which may have been carried over from a previous series). The
    caller can omit `indices` entirely to add books without setting
    indices.
    """
    book_ids = payload.get("book_ids") or []
    if not isinstance(book_ids, list) or not book_ids:
        raise HTTPException(400, "book_ids must be a non-empty list")
    indices = payload.get("indices") or {}

    db = await get_db()
    try:
        await _series_or_404(db, sid)
        added = 0
        for bid in book_ids:
            idx = indices.get(str(bid))
            if idx is not None:
                await db.execute(
                    "UPDATE books SET series_id = ?, series_index = ? "
                    "WHERE id = ?",
                    (sid, idx, bid),
                )
            else:
                await db.execute(
                    "UPDATE books SET series_id = ? WHERE id = ?",
                    (sid, bid),
                )
            added += 1
        await db.commit()
        return {"added": added, "series_id": sid}
    finally:
        await db.close()


@router.delete("/series/{sid}/books/{book_id}")
async def remove_book_from_series(sid: int, book_id: int):
    """Detach a book from this series. Book becomes standalone
    (series_id=NULL, series_index=NULL). 404 if the book isn't
    actually on this series."""
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id FROM books WHERE id = ? AND series_id = ?",
            (book_id, sid),
        )).fetchone()
        if not row:
            raise HTTPException(
                404, f"book {book_id} is not on series {sid}"
            )
        await db.execute(
            "UPDATE books SET series_id = NULL, series_index = NULL "
            "WHERE id = ?", (book_id,),
        )
        await db.commit()
        return {"removed": book_id, "series_id": sid}
    finally:
        await db.close()
