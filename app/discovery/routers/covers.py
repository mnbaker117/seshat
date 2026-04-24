"""
Cover image serving.

Three resolution paths, tried in order:

  1. **Calibre-style** — `cover_path` on the book row points at a local
     `cover.jpg` file mounted into the container; we stream it with a
     `FileResponse`.
  2. **Audiobookshelf** — the book row has `audiobookshelf_id` set but
     no local cover (ABS stores covers inside its own container). We
     proxy ABS's `/api/items/{id}/cover` endpoint, streaming the
     response body back to the browser using the configured bearer
     token.
  3. **Source `cover_url` proxy** — books discovered via Goodreads /
     Hardcover / Amazon / ibdb / etc. have a `cover_url` but no local
     cover_path (we don't cache them on disk). We proxy-stream the
     remote URL so the browser sees a same-origin image and doesn't
     have to care whether it's a CDN URL, a cross-site, or anything
     else.

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
            "SELECT cover_path, audiobookshelf_id, cover_url FROM books WHERE id=?",
            (bid,),
        )).fetchone()
    finally:
        await db.close()

    if row is None:
        raise HTTPException(404)

    abs_id = _safe_str(row, "audiobookshelf_id")
    local_path = _safe_str(row, "cover_path")
    cover_url = _safe_str(row, "cover_url")

    if local_path:
        p = Path(local_path)
        if p.exists():
            return FileResponse(p, media_type="image/jpeg")
        # Fall through to ABS proxy or cover_url if present.

    if abs_id:
        return await _proxy_abs_cover(abs_id)

    if cover_url:
        return await _proxy_cover_url(cover_url)

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


async def _proxy_cover_url(url: str) -> StreamingResponse:
    """Proxy-stream a remote cover image URL back to the browser.

    Used for books discovered via Goodreads / Hardcover / Amazon /
    ibdb / etc. — their `cover_url` points at a third-party CDN and
    we proxy rather than hot-link so:
      * the browser sees a same-origin image (no third-party cookie
        policies, no `referrer` disclosure of our admin UI),
      * the UI can handle a single 404 shape regardless of which
        CDN is behind the book,
      * future work (on-disk caching, format conversion) has a
        single chokepoint to hook into.

    Realistic User-Agent because some image CDNs return 403 for
    clients that look like curl / httpx defaults. 10s timeout is
    plenty for a cover image — most complete in well under a second.
    """
    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(404)

    client = httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                "Gecko/20100101 Firefox/128.0"
            ),
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        },
    )
    try:
        # `stream=True` here by default — `.aiter_bytes()` pulls chunks.
        resp = await client.get(url)
    except Exception:
        await client.aclose()
        raise HTTPException(404)

    if resp.status_code >= 400:
        await client.aclose()
        raise HTTPException(404)

    content_type = resp.headers.get("content-type") or "image/jpeg"
    # Reject content types that clearly aren't images — protects
    # against an upstream URL that serves HTML (e.g. a CAPTCHA page).
    if not content_type.startswith("image/"):
        await client.aclose()
        raise HTTPException(404)

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
