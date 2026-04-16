"""
Cover image fetcher.

Downloads the cover URL a metadata source returned and writes it to
a path under the review staging dir. The review queue row points at
that path so the UI can render thumbnails without re-hitting the
upstream source.

Design notes:
  - Output extension is derived from the Content-Type header (with a
    jpg fallback). We don't trust the URL extension — Goodreads serves
    WebP and JPG interchangeably at URLs ending in `.jpg`.
  - Max size cap (8 MB) to defeat a malicious redirect chain; real
    covers are well under 1 MB.
  - Timeout + single-attempt semantics; the enricher can retry by
    re-calling if needed, and we don't want to dominate the pipeline
    with cover download retries.
  - A failed fetch is logged, returned as None, and the pipeline
    proceeds without a cover — it's a nice-to-have, not required.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

_log = logging.getLogger("seshat.metadata.covers")

_MAX_BYTES = 8 * 1024 * 1024
_TIMEOUT = 15.0

_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


async def fetch_cover(
    cover_url: str,
    *,
    dest_dir: Path,
    basename: str = "cover",
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Path]:
    """Download `cover_url` into `dest_dir/basename.<ext>`.

    Returns the absolute Path on success, or None on any failure.
    Missing cover URLs return None silently — callers don't need a
    guard.
    """
    if not cover_url:
        return None

    owns_client = False
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT, connect=_TIMEOUT / 2),
            follow_redirects=True,
        )
        owns_client = True

    try:
        resp = await client.get(cover_url)
        resp.raise_for_status()
    except Exception as e:
        _log.info("cover fetch failed for %s: %s", cover_url, e)
        if owns_client:
            await _safe_aclose(client)
        return None

    data = resp.content
    if len(data) == 0 or len(data) > _MAX_BYTES:
        _log.info(
            "cover fetch rejected for %s: size %d out of range",
            cover_url, len(data),
        )
        if owns_client:
            await _safe_aclose(client)
        return None

    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = _CONTENT_TYPE_EXT.get(ctype, ".jpg")

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{basename}{ext}"
        dest.write_bytes(data)
    except Exception:
        _log.exception("cover write failed for %s", cover_url)
        if owns_client:
            await _safe_aclose(client)
        return None

    if owns_client:
        await _safe_aclose(client)
    return dest


async def fetch_mam_cover(
    torrent_id: str,
    *,
    dest_dir: Path,
    basename: str = "cover-mam",
    token: str = "",
) -> Optional[Path]:
    """Download a MAM poster image using the cookie-aware client.

    MAM's CDN requires the mam_id cookie, so we go through the
    cookie module's `_do_get` which handles auth + rotation.

    Returns the saved Path, or None on any failure.
    """
    if not torrent_id or not token:
        return None

    from app.mam.torrent_info import mam_cover_url
    from app.mam.cookie import _do_get

    url = mam_cover_url(torrent_id)
    try:
        resp = await _do_get(url, token=token, timeout=15)
        if resp.status_code != 200:
            _log.info("MAM cover fetch HTTP %d for tid=%s", resp.status_code, torrent_id)
            return None
        data = resp.content
        if not data or len(data) < 100 or len(data) > _MAX_BYTES:
            return None
    except Exception as e:
        _log.info("MAM cover fetch failed for tid=%s: %s", torrent_id, e)
        return None

    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = _CONTENT_TYPE_EXT.get(ctype, ".jpg")

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{basename}{ext}"
        dest.write_bytes(data)
        return dest
    except Exception:
        _log.exception("MAM cover write failed for tid=%s", torrent_id)
        return None


async def _safe_aclose(client: httpx.AsyncClient) -> None:
    try:
        await client.aclose()
    except Exception:
        pass
