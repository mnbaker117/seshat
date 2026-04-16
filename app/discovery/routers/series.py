"""
Series endpoints — list and detail.

  GET /api/series         — every series the user has at least one
                            visible book for, with owned/missing
                            counts and multi-author flag
  GET /api/series/{sid}   — full series detail with the ordered
                            book list and per-book ownership state

Both endpoints honor the global hidden-book filter so the totals
shown in the UI match what the user actually sees on book pages.
"""
import logging
from fastapi import APIRouter, HTTPException, Query

from app.discovery.database import get_db, HF

logger = logging.getLogger("seshat.discovery")

router = APIRouter(prefix="/api/discovery", tags=["series"])


@router.get("/series/{sid}")
async def get_series(sid: int):
    db = await get_db()
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
        s["books"] = [dict(b) for b in await (await db.execute(f"""
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
        """, (sid,))).fetchall()]
        return s
    finally:
        await db.close()


@router.get("/series")
async def list_series(search: str = Query(None), sort: str = Query("name"), sort_dir: str = Query("asc"), has_missing: bool = Query(None)):
    db = await get_db()
    try:
        q = f"""SELECT s.*, a.name as author_name,
            COUNT(DISTINCT CASE WHEN {HF} THEN b.id END) as book_count,
            SUM(CASE WHEN b.owned=1 AND {HF} THEN 1 ELSE 0 END) as owned_count,
            SUM(CASE WHEN b.owned=0 AND {HF} THEN 1 ELSE 0 END) as missing_count,
            CASE WHEN COUNT(DISTINCT b.author_id) > 1 THEN 1 ELSE 0 END as multi_author
            FROM series s LEFT JOIN authors a ON s.author_id=a.id LEFT JOIN books b ON s.id=b.series_id"""
        p = []
        c = []
        if search:
            c.append("(s.name LIKE ? OR a.name LIKE ?)")
            p.extend([f"%{search}%"] * 2)
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
