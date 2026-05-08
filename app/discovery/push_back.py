"""
v2.3.5 push-back — Seshat → upstream metadata writes.

Three write paths, dispatched from `metadata.book_push`:

  - `push_abs(db, book, fields)` — `PATCH /api/items/{id}/media`. Always
    available when ABS is configured + the book has an `audiobookshelf_id`.
  - `push_calibre_full(db, book, fields)` — `calibredb set_metadata` per
    field. Requires the `:latest` (full) image; returns
    `PushUnavailable("calibredb not installed")` on slim.
  - `push_cwa(db, book, fields)` — Calibre-Web-Automated form POST at
    `/admin/book/<calibre_id>` (slim path). Requires CWA config.

Each helper returns `PushResult(applied: list[str], failed: list[dict])`.
On success, the corresponding snapshot row is refreshed so the next
`/compare` read shows both DBs in agreement and the cleared
`user_edited_fields` survives the next sync.

The unified dispatcher in `routers/metadata.py` wires these together —
see `book_push` for the routing rules (Calibre push prefers calibredb,
falls back to CWA, 409s if neither is configured).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional

logger = logging.getLogger("seshat.push_back")


class PushUnavailable(Exception):
    """Raised when a push target is not configured / not present in
    this image. Caller translates into HTTP 409 with the message body
    so the UI can show a "configure X in Settings" prompt instead of
    a generic 5xx."""


class PushFailed(Exception):
    """Raised when an upstream push reached its target but the target
    rejected the write (HTTP 4xx/5xx, calibredb non-zero exit, etc.).
    Carries an error string for the user-facing toast."""


# ── ABS push-back ────────────────────────────────────────────────────


# Map of pushable Seshat books column → ABS metadata field. Fields not
# in this map are silently dropped from the push (the caller already
# validates against COMPARE_FIELDS so unknown keys never reach here).
#
# `narrator` and `tags` are stored as comma-separated strings on our
# side but ABS expects JSON arrays — we split on `, ` for both.
# `series_name` + `series_index` collapse into ABS's array-of-objects
# representation (we send a single-element array because ABS treats
# the array as authoritative; multi-series audiobooks are rare and a
# v2.4 problem).
_ABS_FIELD_MAP: dict[str, str] = {
    "title": "title",
    "description": "description",
    "narrator": "narrators",       # CSV → list[str]
    "pub_date": "publishedDate",
    "asin": "asin",
    "isbn": "isbn",
    "language": "language",
    "publisher": "publisher",
    "abridged": "abridged",        # 0/1 → bool
    # series_name / series_index handled specially in _build_abs_metadata
}


def _csv_to_list(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _build_abs_metadata(book_row: dict, fields: list[str]) -> dict:
    """Translate the named Seshat fields into the ABS PATCH body."""
    md: dict = {}
    for f in fields:
        if f == "series_name" or f == "series_index":
            # Bundle series fields into one entry. Triggered once even
            # if both fields are pushed together.
            if "series" in md:
                continue
            name = book_row.get("series_name")
            seq = book_row.get("series_index")
            if name:
                entry: dict = {"name": name}
                if seq is not None:
                    # ABS stores sequence as a string in its UI; keep
                    # the cast simple — int if integral, else float as
                    # string.
                    if float(seq).is_integer():
                        entry["sequence"] = str(int(float(seq)))
                    else:
                        entry["sequence"] = str(seq)
                md["series"] = [entry]
            else:
                # Empty series_name on push = clear all series links.
                md["series"] = []
            continue
        abs_key = _ABS_FIELD_MAP.get(f)
        if not abs_key:
            continue
        val = book_row.get(f)
        if f == "narrator":
            md[abs_key] = _csv_to_list(val)
        elif f == "abridged":
            md[abs_key] = bool(val)
        else:
            md[abs_key] = val
    return md


async def push_abs(db, book_row: dict, fields: list[str]) -> dict:
    """Push the named fields to ABS via PATCH /api/items/{id}/media.

    `book_row` is a dict from the books table (must include
    `audiobookshelf_id` and the resolved `series_name`).

    Returns `{"applied": [...], "failed": [...]}`. Snapshot refreshed
    on success.
    """
    from app.library_apps.audiobookshelf import (
        AudiobookshelfClient,
        _get_abs_api_key,
    )

    abs_id = book_row.get("audiobookshelf_id")
    if not abs_id:
        raise PushUnavailable(
            "this book has no audiobookshelf_id; ABS push not applicable"
        )

    # Resolve base URL from settings — same path the sync uses.
    from app.config import load_settings
    import os
    settings = load_settings()
    base_url = (
        settings.get("abs_url", "") or os.getenv("ABS_URL", "")
    ).rstrip("/")
    if not base_url:
        raise PushUnavailable(
            "Audiobookshelf URL not configured (Settings → Library Apps)"
        )
    api_key = await _get_abs_api_key()
    if not api_key:
        raise PushUnavailable(
            "Audiobookshelf API key not configured (Settings → Credentials)"
        )

    metadata = _build_abs_metadata(book_row, fields)
    if not metadata:
        return {"applied": [], "failed": []}

    client = AudiobookshelfClient(base_url, api_key)
    try:
        await client.patch_item_media(abs_id, {"metadata": metadata})
    except Exception as e:
        # httpx raises HTTPStatusError for non-2xx; bare httpx.HTTPError
        # for connect/timeout. Surface either as a user-facing fail.
        logger.warning("ABS push failed for item %s: %s", abs_id, e)
        raise PushFailed(f"ABS rejected the push: {type(e).__name__}: {e}")

    # Refresh the snapshot from a fresh GET (ABS may have normalized
    # values — narrators trimmed, series sequence rewritten, etc.).
    try:
        item = await client.get_item(abs_id)
        await _refresh_abs_snapshot(db, book_row["id"], item)
    except Exception as e:
        # Non-fatal: the push succeeded; snapshot will catch up on
        # next scheduled sync. Log and continue.
        logger.warning(
            "ABS push succeeded but snapshot refresh failed for item %s: %s",
            abs_id, e,
        )

    return {"applied": list(fields), "failed": []}


async def _refresh_abs_snapshot(db, book_id: int, item: dict) -> None:
    """Write the fresh ABS GET response into books_abs_snapshot.

    Mirrors `audiobookshelf_sync._write_abs_snapshot` shape; reads
    the raw API response (`item["media"]["metadata"]`) directly so we
    don't depend on the sync's `flatten` helper (which adds work we
    don't need for one item).
    """
    media = item.get("media") or {}
    md = media.get("metadata") or {}
    authors = md.get("authors") or []
    authors_json = (
        json.dumps([
            {"id": a.get("id"), "name": a.get("name")}
            for a in authors
        ]) if authors else None
    )
    series_arr = md.get("series") or []
    series_name = series_arr[0].get("name") if series_arr else None
    seq_raw = series_arr[0].get("sequence") if series_arr else None
    try:
        series_index = float(seq_raw) if seq_raw not in (None, "") else None
    except (TypeError, ValueError):
        series_index = None
    narrators = md.get("narrators") or []
    narrator_csv = ", ".join(narrators) if narrators else None

    duration_sec = media.get("duration")
    try:
        duration_sec = int(float(duration_sec)) if duration_sec else None
    except (TypeError, ValueError):
        duration_sec = None

    audio_files = media.get("audioFiles") or []
    formats: set[str] = set()
    for af in audio_files:
        ext = (af.get("metadata") or {}).get("ext") or af.get("ext")
        if ext:
            formats.add(ext.lstrip(".").lower())
    audio_formats = ", ".join(sorted(formats)) or None

    await db.execute("""
        INSERT OR REPLACE INTO books_abs_snapshot
        (book_id, title, authors_json, series_name, series_index,
         narrator, duration_sec, abridged, asin, description, tags,
         cover_path, language, publisher, audio_formats, pubdate, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        book_id,
        md.get("title"),
        authors_json,
        series_name,
        series_index,
        narrator_csv,
        duration_sec,
        1 if md.get("abridged") else 0,
        md.get("asin"),
        md.get("description"),
        None,
        None,
        md.get("language"),
        md.get("publisher"),
        audio_formats,
        md.get("publishedDate"),
        time.time(),
    ))
    await db.commit()


# ── Calibre push-back (full image, calibredb) ────────────────────────


# Map of pushable Seshat books column → calibredb --field name.
# `cover_path` deferred to v2.4.x (needs path plumbing); structural
# fields like `series_index` use Calibre's hyphenated form.
_CALIBREDB_FIELD_MAP: dict[str, str] = {
    "title": "title",
    "description": "comments",     # Calibre's "comments" = description
    "pub_date": "pubdate",
    "isbn": "isbn",
    "language": "languages",       # Calibre stores plural
    "publisher": "publishers",
    "tags": "tags",
    "rating": "rating",
    "series_index": "series_index",
    "series_name": "series",
}


def _format_calibredb_value(field: str, value) -> Optional[str]:
    """Format a Seshat value for `calibredb set_metadata --field NAME:VALUE`.

    Returns None to skip the field (empty / None / unrepresentable).
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        return v
    if field == "rating":
        # Calibre stores 0-10 (half-star integer); books.rating is REAL.
        try:
            return str(int(round(float(value))))
        except (TypeError, ValueError):
            return None
    if field == "series_index":
        try:
            f = float(value)
            return str(int(f)) if f.is_integer() else str(f)
        except (TypeError, ValueError):
            return None
    return str(value)


async def push_calibre_full(db, book_row: dict, fields: list[str]) -> dict:
    """Push the named fields to Calibre via `calibredb set_metadata`.

    Requires the full image (the calibredb binary). Slim image →
    PushUnavailable; caller falls back to CWA push if configured.
    """
    from app.sinks.calibre import CALIBREDB_CMD
    from app.config import CALIBRE_LIBRARY_PATH, load_settings

    cal_id = book_row.get("calibre_id")
    if not cal_id:
        raise PushUnavailable(
            "this book has no calibre_id; Calibre push not applicable"
        )

    settings = load_settings()
    library_path = (
        settings.get("calibre_library_path", "") or CALIBRE_LIBRARY_PATH
    )
    if not library_path:
        raise PushUnavailable(
            "Calibre library path not configured (Settings → Sinks)"
        )

    # Build --field args. Skip fields that format to None.
    args: list[str] = []
    pushed: list[str] = []
    for f in fields:
        cdb_key = _CALIBREDB_FIELD_MAP.get(f)
        if not cdb_key:
            continue
        val = (
            book_row.get("series_name") if f == "series_name"
            else book_row.get(f)
        )
        formatted = _format_calibredb_value(f, val)
        if formatted is None:
            # Skip — calibredb has no portable "clear field" syntax for
            # most fields, and an empty string can be a no-op or a
            # silent corruption depending on the field. We document
            # this as a known limitation: pushing an empty field via
            # calibredb is a no-op; users wanting a true clear use the
            # Calibre UI directly.
            continue
        args.extend(["--field", f"{cdb_key}:{formatted}"])
        pushed.append(f)

    if not args:
        return {"applied": [], "failed": []}

    cmd = [
        CALIBREDB_CMD, "set_metadata",
        "--library-path", library_path,
        *args,
        str(cal_id),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=60,
        )
    except FileNotFoundError:
        raise PushUnavailable(
            "calibredb not found in this image — Calibre push needs the "
            "full :latest image, not :latest-slim. Use CWA push instead."
        )
    except asyncio.TimeoutError:
        raise PushFailed("calibredb set_metadata timed out after 60s")

    err = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        out = stdout.decode("utf-8", errors="replace").strip()
        raise PushFailed(
            f"calibredb set_metadata exit {proc.returncode}: {err or out}"
        )

    # Refresh the snapshot from a fresh metadata.db read for this book.
    try:
        await _refresh_calibre_snapshot(db, book_row["id"], int(cal_id))
    except Exception as e:
        logger.warning(
            "Calibre push succeeded but snapshot refresh failed for "
            "calibre_id=%s: %s", cal_id, e,
        )

    return {"applied": pushed, "failed": []}


async def _refresh_calibre_snapshot(
    db, book_id: int, calibre_id: int,
) -> None:
    """Re-read this one book from Calibre's metadata.db and refresh
    `books_calibre_snapshot`. Mirrors `_write_calibre_snapshot` shape.
    """
    import sqlite3
    from pathlib import Path
    from app.config import CALIBRE_DB_PATH

    if not Path(CALIBRE_DB_PATH).exists():
        return  # Nothing to refresh from.

    # Read the single book + its joined fields. Synchronous because
    # sqlite3 in async land needs aiosqlite, and this is a one-shot.
    def _read() -> Optional[dict]:
        conn = sqlite3.connect(f"file:{CALIBRE_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            bk = conn.execute("""
                SELECT b.id, b.title, b.pubdate, b.series_index,
                       COALESCE(c.text, '') as comments
                FROM books b LEFT JOIN comments c ON c.book = b.id
                WHERE b.id = ?
            """, (calibre_id,)).fetchone()
            if not bk:
                return None
            authors = conn.execute("""
                SELECT a.id, a.name, a.sort
                FROM books_authors_link bal
                JOIN authors a ON bal.author = a.id
                WHERE bal.book = ?
            """, (calibre_id,)).fetchall()
            srow = conn.execute("""
                SELECT s.id, s.name FROM books_series_link bsl
                JOIN series s ON bsl.series = s.id
                WHERE bsl.book = ? LIMIT 1
            """, (calibre_id,)).fetchone()
            isbn = conn.execute("""
                SELECT val FROM identifiers
                WHERE book = ? AND type = 'isbn' LIMIT 1
            """, (calibre_id,)).fetchone()
            tags = conn.execute("""
                SELECT t.name FROM books_tags_link btl
                JOIN tags t ON btl.tag = t.id
                WHERE btl.book = ?
            """, (calibre_id,)).fetchall()
            rating = conn.execute("""
                SELECT r.rating FROM books_ratings_link brl
                JOIN ratings r ON brl.rating = r.id
                WHERE brl.book = ? LIMIT 1
            """, (calibre_id,)).fetchone()
            languages = conn.execute("""
                SELECT l.lang_code FROM books_languages_link bll
                JOIN languages l ON bll.lang_code = l.id
                WHERE bll.book = ? LIMIT 1
            """, (calibre_id,)).fetchone()
            publisher = conn.execute("""
                SELECT p.name FROM books_publishers_link bpl
                JOIN publishers p ON bpl.publisher = p.id
                WHERE bpl.book = ? LIMIT 1
            """, (calibre_id,)).fetchone()
            formats = conn.execute("""
                SELECT format FROM data WHERE book = ?
            """, (calibre_id,)).fetchall()
            return {
                "title": bk["title"],
                "pubdate": bk["pubdate"],
                "series_index": bk["series_index"],
                "description": bk["comments"] or None,
                "authors": [
                    {"id": a["id"], "name": a["name"], "sort": a["sort"]}
                    for a in authors
                ],
                "series_name": srow["name"] if srow else None,
                "isbn": isbn["val"] if isbn else None,
                "tags": ", ".join(t["name"] for t in tags) if tags else None,
                "rating": rating["rating"] if rating else None,
                "language": languages["lang_code"] if languages else None,
                "publisher": publisher["name"] if publisher else None,
                "formats": ", ".join(
                    f["format"].lower() for f in formats
                ) if formats else None,
            }
        finally:
            conn.close()

    book = await asyncio.to_thread(_read)
    if not book:
        return

    authors_json = (
        json.dumps([
            {"id": a["id"], "name": a["name"], "sort": a["sort"]}
            for a in book["authors"]
        ]) if book["authors"] else None
    )
    rating_int = (
        int(round(book["rating"])) if book["rating"] is not None else None
    )
    await db.execute("""
        INSERT OR REPLACE INTO books_calibre_snapshot
        (book_id, title, authors_json, series_name, series_index, isbn,
         cover_path, description, tags, rating, language, publisher,
         formats, pubdate, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        book_id, book["title"], authors_json, book["series_name"],
        book["series_index"], book["isbn"],
        # Cover path not refreshed — we don't push it, and the existing
        # snapshot row's cover_path is still valid.
        None,
        book["description"], book["tags"], rating_int, book["language"],
        book["publisher"], book["formats"], book["pubdate"],
        time.time(),
    ))
    # Preserve cover_path from the existing row (the INSERT OR REPLACE
    # above writes NULL for it, but we want to keep what was there).
    await db.execute("""
        UPDATE books_calibre_snapshot SET cover_path = (
            SELECT cover_path FROM books_calibre_snapshot
            WHERE book_id = ? AND cover_path IS NOT NULL
            ORDER BY synced_at DESC LIMIT 1
        ) WHERE book_id = ? AND cover_path IS NULL
    """, (book_id, book_id))
    await db.commit()


# ── CWA push-back (slim image, /admin/book/<id> form POST) ───────────


# Map of Seshat books column → CWA form field name (per
# upstream calibre-web `editbooks.do_edit_book`). Cover deferred to
# v2.4.x so we don't try to expose a Seshat-served URL through CWA.
_CWA_FIELD_MAP: dict[str, str] = {
    "title": "book_title",
    "description": "comments",
    "pub_date": "pubdate",
    "isbn": None,                 # set via identifier-type/val triplet
    "language": "languages",
    "publisher": "publisher",
    "tags": "tags",
    "rating": "rating",
    "series_name": "series",
    "series_index": "series_index",
}


_CSRF_RX = re.compile(
    r'<input[^>]+name=["\']csrf_token["\'][^>]+value=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


class CWAClient:
    """Login + CSRF-aware client for Calibre-Web-Automated's admin form
    POST endpoint. Caches session cookie + token in-memory for the
    request lifetime.
    """

    def __init__(self, base_url: str, username: str, password: str,
                 timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._cookies: Optional[dict] = None
        self._csrf: Optional[str] = None

    async def _login(self, http) -> None:
        """POST /login, capture session cookie."""
        # GET /login first to grab the CSRF token from the login form
        # (required because login is itself CSRF-protected).
        login_get = await http.get(f"{self.base_url}/login")
        login_get.raise_for_status()
        m = _CSRF_RX.search(login_get.text or "")
        login_csrf = m.group(1) if m else ""
        resp = await http.post(
            f"{self.base_url}/login",
            data={
                "username": self.username,
                "password": self.password,
                "csrf_token": login_csrf,
                "submit": "",
                "next": "/",
            },
            headers={"X-CSRFToken": login_csrf},
        )
        # On success CWA returns 302 to / or 200 with the homepage. A
        # bad password renders the login page again (200 with the
        # form). We detect by checking for the session cookie.
        if "session" not in resp.cookies and "session" not in http.cookies:
            raise PushFailed("CWA login rejected (check username/password)")
        # Persist the cookies on the client object so subsequent calls
        # in this session reuse them. httpx clients track them
        # automatically; we capture for log-and-recover.
        self._cookies = dict(http.cookies)

    async def _scrape_csrf(self, http, book_id: int) -> str:
        """GET /admin/book/<id> and scrape the form's CSRF token."""
        page = await http.get(f"{self.base_url}/admin/book/{book_id}")
        page.raise_for_status()
        m = _CSRF_RX.search(page.text or "")
        if not m:
            raise PushFailed(
                "could not scrape CSRF token from CWA admin/book page "
                "(CWA may have changed its template — pin a known-good "
                "version and retry)"
            )
        self._csrf = m.group(1)
        return self._csrf

    async def push(self, book_id: int, form: dict) -> None:
        """POST /admin/book/<id> with the given form fields."""
        import httpx
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True,
        ) as http:
            await self._login(http)
            csrf = await self._scrape_csrf(http, book_id)
            payload = {
                **form,
                "csrf_token": csrf,
                # Disable CWA's auto-sort heuristics — we want our
                # explicit edits to land verbatim, not get rewritten.
                "checkA": "false",
                "checkT": "false",
            }
            resp = await http.post(
                f"{self.base_url}/admin/book/{book_id}",
                data=payload,
                headers={"X-CSRFToken": csrf},
            )
            if resp.status_code >= 400:
                raise PushFailed(
                    f"CWA returned HTTP {resp.status_code} on book edit"
                )


def _format_cwa_value(field: str, value) -> Optional[str]:
    """Stringify a Seshat value for CWA's form fields. Returns None to
    skip the field entirely (CWA's `pubdate` and similar treat empty
    string as a no-op; we never want to accidentally clear something).
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    if field == "rating":
        try:
            return str(int(round(float(value))))
        except (TypeError, ValueError):
            return None
    if field == "series_index":
        try:
            f = float(value)
            return str(int(f)) if f.is_integer() else str(f)
        except (TypeError, ValueError):
            return None
    return str(value)


async def push_cwa(db, book_row: dict, fields: list[str]) -> dict:
    """Push the named fields to CWA via /admin/book/<calibre_id>."""
    from app.config import load_settings
    from app.secrets import get_secret

    cal_id = book_row.get("calibre_id")
    if not cal_id:
        raise PushUnavailable(
            "this book has no calibre_id; CWA push not applicable"
        )

    settings = load_settings()
    base_url = (settings.get("cwa_base_url") or "").rstrip("/")
    username = settings.get("cwa_username") or ""
    password = await get_secret("cwa_password") or ""
    if not (base_url and username and password):
        raise PushUnavailable(
            "CWA push not configured. Set cwa_base_url, cwa_username, "
            "and cwa_password in Settings → Sinks."
        )

    # Build form body. CWA's form POST handles all fields in one round-
    # trip; we skip any field that can't be represented (None / empty).
    form: dict = {}
    pushed: list[str] = []
    for f in fields:
        if f == "isbn":
            v = _format_cwa_value(f, book_row.get(f))
            if v:
                # CWA expects identifier-type-N + identifier-val-N pairs;
                # one ISBN is enough.
                form["identifier-type-0"] = "isbn"
                form["identifier-val-0"] = v
                pushed.append(f)
            continue
        cwa_key = _CWA_FIELD_MAP.get(f)
        if not cwa_key:
            continue
        v = _format_cwa_value(
            f, book_row.get("series_name") if f == "series_name"
            else book_row.get(f),
        )
        if v is None:
            continue
        form[cwa_key] = v
        pushed.append(f)

    if not form:
        return {"applied": [], "failed": []}

    client = CWAClient(base_url, username, password)
    try:
        await client.push(int(cal_id), form)
    except PushFailed:
        raise
    except Exception as e:
        logger.warning("CWA push raised: %s", e)
        raise PushFailed(f"CWA push failed: {type(e).__name__}: {e}")

    # Refresh the snapshot from Calibre's metadata.db (CWA writes there
    # synchronously). If the slim image has the metadata.db mounted
    # read-only via CALIBRE_DB_PATH this works; if not, we silently
    # skip and let the next scheduled sync catch up.
    try:
        await _refresh_calibre_snapshot(db, book_row["id"], int(cal_id))
    except Exception as e:
        logger.warning(
            "CWA push succeeded but snapshot refresh failed for "
            "calibre_id=%s: %s", cal_id, e,
        )

    return {"applied": pushed, "failed": []}
