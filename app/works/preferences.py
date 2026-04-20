"""
Per-author format tracking preferences.

Global default lives in `settings.audiobook_tracking_mode` ("ebook",
"audiobook", or "both"). Per-author overrides live in
`author_format_preferences` (pipeline DB). Keyed by normalized name so
"Brandon Sanderson" in Calibre and "Brandon Sanderson" in ABS share
the same preference — that's the whole point of per-author prefs
being cross-library.

`effective_tracking_mode(author_name)` folds the global default with
any per-author override and returns one of the three literal modes.
Callers (Missing detection, MAM scan filters) use the returned mode
without re-consulting settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import aiosqlite

from app.works.normalize import normalize_author


VALID_MODES = {"ebook", "audiobook", "both"}


@dataclass(frozen=True)
class FormatPreference:
    normalized_name: str
    display_name: str
    tracking_mode: str   # always one of VALID_MODES
    updated_at: float


async def _open() -> aiosqlite.Connection:
    from app.database import get_db
    return await get_db()


def _global_default() -> str:
    """Return the global tracking mode from settings."""
    from app.config import load_settings
    mode = (load_settings().get("audiobook_tracking_mode") or "both").lower()
    return mode if mode in VALID_MODES else "both"


async def get_preference(
    author_name: str, *, db: Optional[aiosqlite.Connection] = None
) -> Optional[FormatPreference]:
    """Return the explicit preference for an author, or None if unset."""
    norm = normalize_author(author_name)
    if not norm:
        return None
    close_after = db is None
    if db is None:
        db = await _open()
    try:
        row = await (await db.execute(
            "SELECT normalized_name, display_name, tracking_mode, updated_at "
            "FROM author_format_preferences WHERE normalized_name = ?",
            (norm,),
        )).fetchone()
    finally:
        if close_after:
            await db.close()
    return _row_to_pref(row) if row else None


async def set_preference(
    author_name: str,
    tracking_mode: str,
    *,
    db: Optional[aiosqlite.Connection] = None,
) -> None:
    """Upsert a tracking preference for an author."""
    if tracking_mode not in VALID_MODES:
        raise ValueError(
            f"tracking_mode must be one of {sorted(VALID_MODES)}, "
            f"got {tracking_mode!r}"
        )
    norm = normalize_author(author_name)
    if not norm:
        raise ValueError("author_name must be non-empty after normalization")
    close_after = db is None
    if db is None:
        db = await _open()
    try:
        await db.execute(
            "INSERT INTO author_format_preferences "
            "(normalized_name, display_name, tracking_mode) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(normalized_name) DO UPDATE SET "
            "display_name = excluded.display_name, "
            "tracking_mode = excluded.tracking_mode, "
            "updated_at = strftime('%s','now')",
            (norm, author_name.strip(), tracking_mode),
        )
        if close_after:
            await db.commit()
    finally:
        if close_after:
            await db.close()


async def clear_preference(
    author_name: str, *, db: Optional[aiosqlite.Connection] = None
) -> bool:
    """Drop the per-author override — subsequent reads inherit the global."""
    norm = normalize_author(author_name)
    if not norm:
        return False
    close_after = db is None
    if db is None:
        db = await _open()
    try:
        cur = await db.execute(
            "DELETE FROM author_format_preferences WHERE normalized_name = ?",
            (norm,),
        )
        if close_after:
            await db.commit()
        return (cur.rowcount or 0) > 0
    finally:
        if close_after:
            await db.close()


async def list_preferences(
    *, db: Optional[aiosqlite.Connection] = None,
) -> list[FormatPreference]:
    close_after = db is None
    if db is None:
        db = await _open()
    try:
        rows = await (await db.execute(
            "SELECT normalized_name, display_name, tracking_mode, updated_at "
            "FROM author_format_preferences ORDER BY display_name"
        )).fetchall()
    finally:
        if close_after:
            await db.close()
    return [_row_to_pref(r) for r in rows]


async def effective_tracking_mode(
    author_name: str, *, db: Optional[aiosqlite.Connection] = None
) -> str:
    """Return the mode actually in effect for an author.

    Per-author override wins; otherwise the global default applies.
    Always returns one of `VALID_MODES` (never None).
    """
    pref = await get_preference(author_name, db=db)
    if pref is not None:
        return pref.tracking_mode
    return _global_default()


def _row_to_pref(row) -> FormatPreference:
    return FormatPreference(
        normalized_name=row["normalized_name"],
        display_name=row["display_name"],
        tracking_mode=row["tracking_mode"],
        updated_at=row["updated_at"],
    )
