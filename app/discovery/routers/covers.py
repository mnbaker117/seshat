"""
Cover image serving.

Calibre stores book covers as `cover.jpg` inside each book's folder
on disk. Rather than re-uploading every cover into AthenaScout's own
storage during sync, we resolve them on demand: the book row carries
the absolute `cover_path`, and `/api/covers/{bid}` reads and streams
that file when the UI asks for it. Source-discovered books that
don't yet have a local file fall back to `cover_url` from the source.
"""
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.discovery.database import get_db

logger = logging.getLogger("seshat.discovery")

router = APIRouter(prefix="/api/discovery", tags=["covers"])


@router.get("/covers/{bid}")
async def get_cover(bid: int):
    db = await get_db()
    try:
        r = await (await db.execute("SELECT cover_path FROM books WHERE id=?", (bid,))).fetchone()
        if not r or not r["cover_path"]:
            raise HTTPException(404)
        p = Path(r["cover_path"])
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg")
    finally:
        await db.close()
