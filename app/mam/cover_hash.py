"""Cover image perceptual hashing for MAM URL verification.

Part C of the MAM URL confidence arc. Given a candidate MAM torrent
and the searched book's cover, compute a perceptual-hash distance
between the two images. Low distance = strong evidence the URL is
correct; high distance = strong evidence it isn't.

The hashing algorithm is **pHash** (DCT-based) via the `imagehash`
library. Validated 2026-05-09 against 16 real cover pairs from Mark's
library: right-Possible matches cluster at Hamming distance 0-6,
wrong matches at 28-36, with a 22-bit empty band between. See
`project_seshat_mam_url_confidence` memory for the full experiment.

Surface:

    Pure helpers (sync, no I/O):
      hash_image_bytes(data)      -> 16-char hex pHash, or None
      hash_image_file(path)       -> 16-char hex pHash, or None
      hamming_distance(a, b)      -> int 0-64

    Cache (async, hits global DB):
      get_cached_cover_hash(tid)  -> hex pHash if fresh, else None
      store_cover_hash(tid, ...)  -> persists in mam_cover_hashes

    Top-level (async, fetches + caches):
      fetch_and_hash_mam_cover(tid, token) -> hex pHash, or None

All failure modes return None — callers MUST treat None as "no
signal available" rather than "verified absent". Cover-pHash is a
promotion-only signal in production today; absence of signal must
not flip status.
"""
import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

import aiosqlite
import imagehash
from PIL import Image, UnidentifiedImageError

from app.config import APP_DB_PATH
from app.mam.cookie import _do_get
from app.mam.torrent_info import mam_cover_url

_log = logging.getLogger("seshat.mam.cover_hash")

# Cache TTL: 30 days. Cover images on MAM rarely change post-upload;
# stale hashes after this window mostly represent edge cases (re-uploads,
# uploader cover swaps) where re-hashing is cheap insurance.
_CACHE_TTL_SEC = 30 * 24 * 3600

# Reject suspiciously small/large image bytes — placeholder pixels or
# multi-MB high-res scans both indicate "not a normal cover".
_MIN_IMAGE_BYTES = 200
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB

# Hamming distance returned when comparison can't be performed (malformed
# inputs). Biases toward "definitely not a match" so callers downstream
# don't accidentally promote on garbage.
_MAX_DISTANCE = 64


def hash_image_bytes(data: bytes) -> Optional[str]:
    """Compute pHash of image bytes. Returns 16-char hex or None on failure.

    Failures (bad bytes, unrecognized format, decode error, size
    sanity-check) are logged at DEBUG and return None. Callers don't
    need to handle exceptions — None just means "no signal available".
    """
    if not data or len(data) < _MIN_IMAGE_BYTES or len(data) > _MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            return str(imagehash.phash(img))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        _log.debug("hash_image_bytes failed (%d bytes): %s", len(data), e)
        return None


def hash_image_file(path) -> Optional[str]:
    """Compute pHash of an image file on disk. Returns 16-char hex or None.

    Used by the Calibre-cover backfill path: read the local
    cover_path file, hash it once, persist on the books row. Same
    failure-mode contract as `hash_image_bytes`.
    """
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        with Image.open(p) as img:
            img.load()
            return str(imagehash.phash(img))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        _log.debug("hash_image_file failed for %s: %s", p, e)
        return None


def hamming_distance(hash_a: Optional[str], hash_b: Optional[str]) -> int:
    """Hamming distance between two hex-encoded 64-bit pHashes (0-64).

    Returns `_MAX_DISTANCE` (64) if either input is missing or
    malformed. The bias toward max-distance means a malformed cache
    value never accidentally triggers promotion.

    Note: `imagehash.hex_to_hash("ab")` parses as valid hex but yields
    a non-64-bit hash that raises TypeError on subtraction with a
    standard 64-bit hash. The subtraction is wrapped in the same
    try-block to absorb that case.
    """
    if not hash_a or not hash_b:
        return _MAX_DISTANCE
    try:
        a = imagehash.hex_to_hash(hash_a)
        b = imagehash.hex_to_hash(hash_b)
        # Coerce to native Python int — `imagehash.__sub__` returns
        # `numpy.int64`, which is comparable to Python int but not
        # JSON-serializable by Pydantic. Production hit this serializing
        # debug-match traces with `cover_check.distance` populated.
        return int(a - b)
    except (ValueError, TypeError):
        return _MAX_DISTANCE


# ─── Persistent cache (global DB, mam_cover_hashes table) ────────


async def get_cached_cover_hash(
    torrent_id: str,
    *,
    db: Optional[aiosqlite.Connection] = None,
) -> Optional[str]:
    """Read a cover hash from the persistent cache, respecting TTL.

    Returns the cached pHash if fresh (within `_CACHE_TTL_SEC`),
    else None. Pass an open `db` connection to batch lookups across
    multiple candidates without per-call connection setup overhead.
    """
    if not torrent_id:
        return None
    close_after = db is None
    if db is None:
        db = await aiosqlite.connect(str(APP_DB_PATH))
        db.row_factory = aiosqlite.Row
    try:
        cur = await db.execute(
            "SELECT phash, fetched_at FROM mam_cover_hashes WHERE torrent_id = ?",
            (str(torrent_id),),
        )
        row = await cur.fetchone()
        if not row:
            return None
        if time.time() - float(row["fetched_at"]) > _CACHE_TTL_SEC:
            return None
        return row["phash"]
    finally:
        if close_after:
            await db.close()


async def store_cover_hash(
    torrent_id: str,
    phash: str,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    bytes_count: Optional[int] = None,
    db: Optional[aiosqlite.Connection] = None,
) -> None:
    """Insert or replace a cover hash in the persistent cache.

    `width`, `height`, `bytes_count` are optional diagnostic columns —
    useful when investigating "why did this hash compare oddly" cases
    via SQL.
    """
    if not torrent_id or not phash:
        return
    close_after = db is None
    if db is None:
        db = await aiosqlite.connect(str(APP_DB_PATH))
    try:
        await db.execute(
            "INSERT OR REPLACE INTO mam_cover_hashes "
            "(torrent_id, phash, fetched_at, width, height, bytes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(torrent_id), phash, time.time(), width, height, bytes_count),
        )
        await db.commit()
    finally:
        if close_after:
            await db.close()


# ─── Top-level: fetch + hash + cache ─────────────────────────────


async def fetch_and_hash_mam_cover(
    torrent_id: str,
    token: str,
    *,
    db: Optional[aiosqlite.Connection] = None,
) -> Optional[str]:
    """Fetch a MAM cover, compute pHash, persist + return.

    Reads the cache first; on hit returns the cached value without an
    HTTP fetch. On miss, fetches via the cookie-aware client (mam_id
    auth, same as the search API), hashes, persists, and returns.

    Returns None on any failure (network, auth, decode, image-too-small)
    — callers MUST treat None as "no signal available" rather than
    "verified absent". The MAM CDN occasionally serves transient errors
    or empty responses; promotion logic must not regress on these.
    """
    if not torrent_id or not token:
        return None
    cached = await get_cached_cover_hash(torrent_id, db=db)
    if cached:
        return cached
    url = mam_cover_url(str(torrent_id))
    try:
        resp = await _do_get(url, token=token, timeout=15)
    except Exception as e:
        _log.info("MAM cover fetch failed for tid=%s: %s", torrent_id, e)
        return None
    if resp.status_code != 200:
        _log.info("MAM cover fetch HTTP %d for tid=%s", resp.status_code, torrent_id)
        return None
    data = resp.content
    if not data:
        return None
    phash = hash_image_bytes(data)
    if phash is None:
        return None
    width = height = None
    try:
        with Image.open(BytesIO(data)) as img:
            width, height = img.size
    except Exception:
        pass
    await store_cover_hash(
        str(torrent_id), phash,
        width=width, height=height, bytes_count=len(data), db=db,
    )
    return phash
