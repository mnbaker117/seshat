"""
Cover image serving.

Two resolution paths:

  1. **Calibre-style** — `cover_path` on the book row points at a local
     `cover.jpg` file mounted into the container; we stream it with a
     `FileResponse`.
  2. **Audiobookshelf** — the book row has `audiobookshelf_id` set but
     no local cover (ABS stores covers inside its own container). We
     proxy ABS's `/api/items/{id}/cover` endpoint, streaming the
     response body back to the browser using the configured bearer
     token.

Two endpoint shapes:

    GET /api/discovery/covers/{bid}          — active library only (legacy)
    GET /api/discovery/covers/{slug}/{bid}   — explicit per-library lookup

The slug-scoped variant is what the cross-library aggregated views
(Library Audiobooks tab, Works page) hit so they can resolve covers
from a library that isn't currently active.
"""
import logging
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.discovery.database import get_db

logger = logging.getLogger("seshat.discovery")

router = APIRouter(prefix="/api/discovery", tags=["covers"])


async def _resolve_cover(slug: Optional[str], bid: int):
    """Look up the book, return either a FileResponse or StreamingResponse.

    Raises HTTPException on every failure path so the caller doesn't
    have to think about branching — 404 is the unified "no cover here"
    signal regardless of whether the book was missing, had no cover
    path, the file was gone, or the ABS proxy call returned non-2xx.
    """
    db = await get_db(slug) if slug else await get_db()
    try:
        row = await (await db.execute(
            "SELECT cover_path, audiobookshelf_id FROM books WHERE id=?",
            (bid,),
        )).fetchone()
    finally:
        await db.close()

    if row is None:
        raise HTTPException(404)

    abs_id = _safe_str(row, "audiobookshelf_id")
    local_path = _safe_str(row, "cover_path")

    if local_path:
        p = Path(local_path)
        if p.exists():
            return FileResponse(p, media_type="image/jpeg")
        # fall through to ABS proxy if we have an audiobookshelf_id

    if abs_id:
        return await _proxy_abs_cover(abs_id)

    raise HTTPException(404)


def _safe_str(row, key: str) -> str:
    """Row accessor that tolerates missing columns.

    Older DBs (pre-Phase 1) don't have `audiobookshelf_id` at all. We
    catch IndexError / KeyError instead of assuming the column is there
    so this router stays compatible with pre-migration snapshots.
    """
    try:
        val = row[key]
    except (IndexError, KeyError):
        return ""
    return val or ""


async def _proxy_abs_cover(abs_item_id: str) -> StreamingResponse:
    """Stream ABS's `/api/items/{id}/cover` back to the browser.

    Reads `abs_url` + `abs_api_key` fresh on each request — cheap, and
    avoids a stale-credentials bug if the user updates either setting
    mid-session. Errors turn into 404 so the UI's `onError` handler
    falls through to the placeholder glyph cleanly.
    """
    from app.config import load_settings
    from app.secrets import get_secret

    settings = load_settings()
    base = (settings.get("abs_url") or "").rstrip("/")
    api_key = await get_secret("abs_api_key")
    if not base or not api_key:
        raise HTTPException(404)

    url = f"{base}/api/items/{abs_item_id}/cover"
    client = httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(
            url, headers={"Authorization": f"Bearer {api_key}"},
        )
    except Exception:
        await client.aclose()
        raise HTTPException(404)

    if resp.status_code >= 400:
        await client.aclose()
        raise HTTPException(404)

    content_type = resp.headers.get("content-type", "image/jpeg")

    # Stream the body back while holding the client open; close it
    # once the iterator completes (success) or the request is
    # cancelled (client disconnect).
    async def iter_body():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await client.aclose()

    return StreamingResponse(iter_body(), media_type=content_type)


@router.get("/covers/{bid}")
async def get_cover(bid: int):
    """Active-library cover resolution — legacy shape."""
    return await _resolve_cover(None, bid)


@router.get("/covers/{slug}/{bid}")
async def get_cover_for_library(slug: str, bid: int):
    """Per-library cover resolution — used by cross-library views."""
    return await _resolve_cover(slug, bid)
