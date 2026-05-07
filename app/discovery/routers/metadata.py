"""
v2.3.4 Metadata Manager backend.

Surfaces the dual-storage data model (`books`, `books_calibre_snapshot`,
`books_abs_snapshot`, `metadata_review_queue`) to two front-end pieces:

  - **Compare panel** (book sidebar) — `/books/{bid}/compare` returns
    Seshat-live + Calibre snapshot + ABS snapshot side-by-side, with
    per-field diff flags for UI highlighting. `/books/{bid}/pull`
    copies one or more snapshot fields into Seshat-live and flags
    them as user-edited so the next sync's auto-flow doesn't roll
    the change back.

  - **Metadata Manager page** (top-level) — `/queue` lists pending
    review-queue rows grouped by source, `/queue/{id}/apply` writes
    `new_value` to the books table and deletes the queue row,
    `/queue/{id}/dismiss` deletes the row without writing.

The legacy `series-suggestions` table stays — `/queue/series-moves`
exposes it under the same review-queue mental model so the
Suggestions page can retire (its functionality folds into the
Metadata Manager's "Series moves" tab).
"""
import json
import logging
from fastapi import APIRouter, Body, HTTPException, Query

from app.discovery.database import get_db

logger = logging.getLogger("seshat.discovery")

router = APIRouter(prefix="/api/discovery", tags=["metadata"])


# ── Field map: (books column, calibre snapshot column, abs snapshot column, label) ──
#
# Determines which fields the Compare panel surfaces. Order in this
# list = render order in the UI. Calibre's `pubdate` and ABS's
# `pubdate` both map to `pub_date` on the books table; the snapshot
# tables kept Calibre's column name for the snapshot to mirror its
# source schema verbatim.
COMPARE_FIELDS: list[tuple[str, str | None, str | None, str]] = [
    # books_col,        calibre_col,    abs_col,        label
    ("title",           "title",        "title",        "Title"),
    ("description",     "description",  "description",  "Description"),
    ("pub_date",        "pubdate",      "pubdate",      "Publication date"),
    ("isbn",            "isbn",         None,           "ISBN"),
    ("series_index",    "series_index", "series_index", "Series #"),
    ("tags",            "tags",         "tags",         "Tags"),
    ("language",        "language",     "language",     "Language"),
    ("publisher",       "publisher",    "publisher",    "Publisher"),
    ("cover_path",      "cover_path",   "cover_path",   "Cover path"),
    ("rating",          "rating",       None,           "Rating"),
    ("formats",         "formats",      None,           "Formats"),
    ("narrator",        None,           "narrator",     "Narrator"),
    ("duration_sec",    None,           "duration_sec", "Duration (s)"),
    ("abridged",        None,           "abridged",     "Abridged"),
    ("asin",            None,           "asin",         "ASIN"),
    ("audio_formats",   None,           "audio_formats", "Audio formats"),
]


def _parse_user_edited(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


@router.get("/books/{bid}/compare")
async def book_compare(bid: int, slug: str | None = Query(None)):
    """Return Seshat-live + Calibre snapshot + ABS snapshot for one
    book, side-by-side. Per-field `calibre_diff` / `abs_diff` flags
    let the UI highlight cells that differ from Seshat-live.

    Snapshot rows may be missing (book never came from Calibre / ABS)
    — those columns return null and `*_diff` is false everywhere.

    `slug` query param routes the read to a specific library —
    snapshots are per-library so passing the book's library_slug
    avoids reading a different library's row that happens to share
    the numeric id (see books.update_book for the same rationale).
    """
    db = await get_db(slug)
    try:
        book_row = await (await db.execute(
            "SELECT * FROM books WHERE id = ?", (bid,),
        )).fetchone()
        if not book_row:
            raise HTTPException(404, f"book {bid} not found")
        book = dict(book_row)
        # Resolve Seshat-live series name via the series table — the
        # books column is series_id (FK), but the snapshot tables
        # store series_name as text. The Compare panel surfaces the
        # name so the user can pull a Calibre/ABS series back to
        # Seshat-live (Mark's UAT 2026-05-07: post-recovery he had
        # to re-attach the series manually because Compare didn't
        # show it).
        seshat_series_name: str | None = None
        if book.get("series_id"):
            srow = await (await db.execute(
                "SELECT name FROM series WHERE id = ?", (book["series_id"],),
            )).fetchone()
            seshat_series_name = srow["name"] if srow else None
        cal_row = await (await db.execute(
            "SELECT * FROM books_calibre_snapshot WHERE book_id = ?", (bid,),
        )).fetchone()
        abs_row = await (await db.execute(
            "SELECT * FROM books_abs_snapshot WHERE book_id = ?", (bid,),
        )).fetchone()
        cal = dict(cal_row) if cal_row else None
        abs_ = dict(abs_row) if abs_row else None

        user_edited = _parse_user_edited(book.get("user_edited_fields"))

        fields_out: list[dict] = []
        for books_col, cal_col, abs_col, label in COMPARE_FIELDS:
            seshat_v = book.get(books_col)
            cal_v = cal.get(cal_col) if (cal and cal_col) else None
            abs_v = abs_.get(abs_col) if (abs_ and abs_col) else None
            # Skip rows where every value is empty — saves the UI
            # from rendering empty rows for ABS-only fields on
            # ebook-only books, etc.
            if seshat_v in (None, "") and cal_v in (None, "") and abs_v in (None, ""):
                continue
            fields_out.append({
                "field": books_col,
                "label": label,
                "seshat": seshat_v,
                "calibre": cal_v,
                "abs": abs_v,
                "calibre_diff": cal_col is not None
                    and cal is not None
                    and cal_v != seshat_v,
                "abs_diff": abs_col is not None
                    and abs_ is not None
                    and abs_v != seshat_v,
                "user_edited": books_col in user_edited,
            })

        # v2.3.4.4: synthetic Series row — books table has series_id,
        # snapshots have series_name. Compare displays the resolved
        # name; pull resolves snapshot's name → series_id via
        # find-or-create. Inserted right after the Series # row so
        # the two related fields render together in the UI.
        cal_series = cal.get("series_name") if cal else None
        abs_series = abs_.get("series_name") if abs_ else None
        if not (
            seshat_series_name in (None, "")
            and cal_series in (None, "")
            and abs_series in (None, "")
        ):
            series_field = {
                "field": "series_name",
                "label": "Series",
                "seshat": seshat_series_name,
                "calibre": cal_series,
                "abs": abs_series,
                "calibre_diff": cal is not None
                    and cal_series != seshat_series_name,
                "abs_diff": abs_ is not None
                    and abs_series != seshat_series_name,
                "user_edited": "series_name" in user_edited,
            }
            # Insert just before series_index for a logical UI order.
            inserted = False
            for i, f in enumerate(fields_out):
                if f["field"] == "series_index":
                    fields_out.insert(i, series_field)
                    inserted = True
                    break
            if not inserted:
                fields_out.append(series_field)

        return {
            "book_id": bid,
            "user_edited_fields": user_edited,
            "calibre_synced_at": cal.get("synced_at") if cal else None,
            "abs_synced_at": abs_.get("synced_at") if abs_ else None,
            "fields": fields_out,
        }
    finally:
        await db.close()


@router.post("/books/{bid}/pull")
async def book_pull(bid: int, payload: dict = Body(...), slug: str | None = Query(None)):
    """Pull one or more snapshot fields into Seshat-live.

    Request body:
      {"source": "calibre" | "abs",
       "fields": ["description", "pub_date", ...]}

    Each named field is copied from the snapshot to the corresponding
    books column. Field names use the BOOKS column name (which is
    what the Compare endpoint exposes), not the snapshot column —
    the mapping happens here. Pulled fields are added to
    `books.user_edited_fields` so the next Calibre/ABS sync's
    auto-flow doesn't immediately roll the value back. (The user
    explicitly chose this value; treat as a manual edit for
    auto-flow purposes.)

    400 if source is invalid, 404 if the snapshot doesn't exist.
    """
    source = payload.get("source")
    fields = payload.get("fields") or []
    if source not in ("calibre", "abs"):
        raise HTTPException(400, "source must be 'calibre' or 'abs'")
    if not isinstance(fields, list) or not fields:
        raise HTTPException(400, "fields must be a non-empty list")

    snapshot_table = (
        "books_calibre_snapshot" if source == "calibre"
        else "books_abs_snapshot"
    )
    # Field map for this source — books_col → snapshot_col.
    if source == "calibre":
        col_map = {b: c for b, c, _, _ in COMPARE_FIELDS if c is not None}
    else:
        col_map = {b: a for b, _, a, _ in COMPARE_FIELDS if a is not None}

    db = await get_db(slug)
    try:
        # 404 the book early. Read author_id too for series resolution.
        b_row = await (await db.execute(
            "SELECT id, author_id, user_edited_fields FROM books WHERE id = ?",
            (bid,),
        )).fetchone()
        if not b_row:
            raise HTTPException(404, f"book {bid} not found")
        # Snapshot must exist.
        snap_row = await (await db.execute(
            f"SELECT * FROM {snapshot_table} WHERE book_id = ?", (bid,),
        )).fetchone()
        if not snap_row:
            raise HTTPException(
                404, f"no {source} snapshot for book {bid}",
            )
        snap = dict(snap_row)

        sets = []
        vals: list = []
        applied: list[str] = []
        for f in fields:
            # v2.3.4.4: special-case series_name — books column is
            # series_id (FK), snapshot has series_name (text). Resolve
            # the snapshot name to a series row (find-or-create
            # author-scoped) and write series_id.
            if f == "series_name":
                snap_name = (snap.get("series_name") or "").strip()
                if not snap_name:
                    # Snapshot has no series — clear the link.
                    sets.append("series_id=?")
                    vals.append(None)
                    applied.append(f)
                    continue
                # Find or create author-scoped series row. Mirrors the
                # lookup used by `update_book` and the calibre_sync
                # pass — author_id matches the book's primary author.
                aid = b_row["author_id"]
                srow = await (await db.execute(
                    "SELECT id FROM series WHERE LOWER(name) = LOWER(?) "
                    "AND author_id = ?", (snap_name, aid),
                )).fetchone()
                if srow:
                    sid = srow["id"]
                else:
                    cur = await db.execute(
                        "INSERT INTO series (name, author_id) VALUES (?, ?)",
                        (snap_name, aid),
                    )
                    sid = cur.lastrowid
                sets.append("series_id=?")
                vals.append(sid)
                applied.append(f)
                continue
            if f not in col_map:
                raise HTTPException(
                    400, f"field '{f}' not pullable from {source}",
                )
            snap_col = col_map[f]
            sets.append(f"{f}=?")
            vals.append(snap.get(snap_col))
            applied.append(f)

        # Merge applied fields into user_edited_fields (set-union).
        existing_uef = _parse_user_edited(b_row["user_edited_fields"])
        merged_uef = sorted(set(existing_uef) | set(applied))
        if set(merged_uef) != set(existing_uef):
            sets.append("user_edited_fields=?")
            vals.append(json.dumps(merged_uef))

        vals.append(bid)
        await db.execute(
            f"UPDATE books SET {', '.join(sets)} WHERE id = ?", vals,
        )
        await db.commit()

        return {
            "book_id": bid,
            "source": source,
            "applied": applied,
            "user_edited_fields": merged_uef,
        }
    finally:
        await db.close()


# ── Metadata Manager — review queue endpoints ────────────────────────


@router.get("/queue")
async def list_queue(
    source: str = Query(None),
    status: str = Query("pending"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List `metadata_review_queue` rows, grouped by source category.

    `source` filters to a specific source name ('calibre', 'abs',
    'goodreads', etc.). Omit for all.

    `status` is currently always 'pending' — the queue table has no
    status column today (rows are created on diff and deleted on
    accept/dismiss), so this param is reserved for future use when
    we add a soft-delete pattern. The Metadata Manager UI surfaces
    a status filter (currently no-op) so the contract is in place.

    Returns rows joined with book + author info for direct render.
    """
    db = await get_db()
    try:
        sql = (
            "SELECT q.id, q.book_id, q.field, q.old_value, q.new_value, "
            "q.source, q.proposed_at, "
            "b.title as book_title, a.name as author_name "
            "FROM metadata_review_queue q "
            "JOIN books b ON b.id = q.book_id "
            "JOIN authors a ON a.id = b.author_id"
        )
        params: list = []
        clauses = []
        if source:
            clauses.append("q.source = ?")
            params.append(source)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY q.proposed_at DESC, q.id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = await (await db.execute(sql, params)).fetchall()

        # Total count (pre-pagination) so the UI can render
        # "showing X of Y" + paginate.
        count_sql = "SELECT COUNT(*) AS n FROM metadata_review_queue"
        count_params: list = []
        if source:
            count_sql += " WHERE source = ?"
            count_params.append(source)
        total = (await (await db.execute(
            count_sql, count_params,
        )).fetchone())["n"]

        return {
            "rows": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        await db.close()


@router.post("/queue/{qid}/apply")
async def queue_apply(qid: int):
    """Accept a queue row: write `new_value` to the corresponding
    books column, add the field to `user_edited_fields`, and delete
    the queue row.

    Coerces TEXT-stored values back to the column's expected type
    where needed (REAL series_index, INTEGER page_count, etc.).
    Returns 400 on type-coerce failure rather than writing garbage.
    """
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, book_id, field, new_value, source "
            "FROM metadata_review_queue WHERE id = ?", (qid,),
        )).fetchone()
        if not row:
            raise HTTPException(404, f"queue row {qid} not found")
        field = row["field"]
        new_val_raw = row["new_value"]

        # Type coercion — mirrors the books column types.
        new_val: object = new_val_raw
        try:
            if field in ("series_index", "duration_sec", "rating"):
                new_val = float(new_val_raw) if new_val_raw is not None else None
            elif field in ("page_count", "abridged", "is_unreleased"):
                new_val = int(new_val_raw) if new_val_raw is not None else None
        except (TypeError, ValueError):
            raise HTTPException(
                400, f"new_value cannot coerce to {field}'s type",
            )

        # Update the books row + merge field into user_edited_fields.
        bid = row["book_id"]
        b_row = await (await db.execute(
            "SELECT user_edited_fields FROM books WHERE id = ?", (bid,),
        )).fetchone()
        if not b_row:
            # Book deleted out from under us — drop the queue row.
            await db.execute(
                "DELETE FROM metadata_review_queue WHERE id = ?", (qid,),
            )
            await db.commit()
            raise HTTPException(404, f"book {bid} not found")
        uef = _parse_user_edited(b_row["user_edited_fields"])
        uef_merged = sorted(set(uef) | {field})
        await db.execute(
            f"UPDATE books SET {field}=?, user_edited_fields=? WHERE id=?",
            (new_val, json.dumps(uef_merged), bid),
        )
        await db.execute(
            "DELETE FROM metadata_review_queue WHERE id = ?", (qid,),
        )
        await db.commit()
        return {"applied": qid, "book_id": bid, "field": field}
    finally:
        await db.close()


@router.post("/queue/{qid}/dismiss")
async def queue_dismiss(qid: int):
    """Reject a queue row: delete it without writing to books."""
    db = await get_db()
    try:
        cur = await db.execute(
            "DELETE FROM metadata_review_queue WHERE id = ?", (qid,),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"queue row {qid} not found")
        return {"dismissed": qid}
    finally:
        await db.close()


@router.post("/queue/bulk")
async def queue_bulk(payload: dict = Body(...)):
    """Bulk apply or dismiss queue rows.

    Body: {"action": "apply" | "dismiss", "ids": [1, 2, 3]}.
    Returns per-id success/failure so the caller can resolve partial
    failures (e.g. one row's book_id was deleted) without abandoning
    the rest.
    """
    action = payload.get("action")
    ids = payload.get("ids") or []
    if action not in ("apply", "dismiss"):
        raise HTTPException(400, "action must be 'apply' or 'dismiss'")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "ids must be a non-empty list")

    results: list[dict] = []
    for qid in ids:
        try:
            if action == "apply":
                await queue_apply(qid)
            else:
                await queue_dismiss(qid)
            results.append({"id": qid, "ok": True})
        except HTTPException as e:
            results.append({"id": qid, "ok": False, "error": str(e.detail)})
    succeeded = sum(1 for r in results if r["ok"])
    return {"results": results, "succeeded": succeeded, "total": len(ids)}
