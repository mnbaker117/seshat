"""
v2.3.6 behavior: MAM rescan widening + auto-release on expected_date.

Two features land together because they both flow through the same
per-library loop in `mam_scheduler_loop`:

  1. Books with `mam_status` = 'possible' or 'not_found' are now
     eligible for rescan (previously only NULL-status books were
     scanned). Catalog churn on MAM means a search that came up
     empty last week may hit today.

  2. Books with `is_unreleased=1` whose `expected_date` has passed
     auto-clear the unreleased flag at the top of each MAM scan
     tick — they stop looking like Upcoming and become normal
     Missing books eligible for MAM scanning the same tick they
     age in.
"""
from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
async def single_library(tmp_path, monkeypatch):
    """One ebook library, registered in `state._discovered_libraries`,
    initialized with the production schema."""
    from app import config as app_config, state
    from app.discovery import database as disco_db

    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(disco_db, "DATA_DIR", tmp_path)

    libs = [
        {"slug": "ebooks", "name": "Ebooks", "display_name": "Ebooks",
         "content_type": "ebook", "app_type": "calibre"},
    ]
    monkeypatch.setattr(state, "_discovered_libraries", libs)

    disco_db.set_active_library("ebooks")
    await disco_db.init_db("ebooks")
    db = await disco_db.get_db(slug="ebooks")
    try:
        await db.execute(
            "INSERT INTO authors (name, sort_name) VALUES ('A', 'A')"
        )
        aid = (await (await db.execute(
            "SELECT last_insert_rowid()"
        )).fetchone())[0]
        yield db, aid
    finally:
        await db.close()
        disco_db.set_active_library(None)


# ─── Feature 2: rescan widening ──────────────────────────────────


async def test_basic_predicate_includes_null_possible_and_not_found(
    single_library,
):
    """The BASIC predicate (used by every scheduled/manual scan) must
    pick up books that are unscanned, possible, OR not_found — but
    not books with `mam_status='found'` (terminal)."""
    from app.discovery.sources.mam import _NEEDS_SCAN_BASIC_BARE

    db, aid = single_library
    # Seed one book per status state.
    await db.execute(
        "INSERT INTO books (title, author_id, mam_status) VALUES "
        "('null-row', ?, NULL),"
        "('possible-row', ?, 'possible'),"
        "('not_found-row', ?, 'not_found'),"
        "('found-row', ?, 'found')",
        (aid, aid, aid, aid),
    )
    await db.commit()

    rows = await db.execute_fetchall(
        f"SELECT title FROM books WHERE {_NEEDS_SCAN_BASIC_BARE}"
    )
    titles = sorted(r[0] for r in rows)
    assert titles == ["not_found-row", "null-row", "possible-row"]


async def test_basic_predicate_excludes_unreleased_and_hidden(
    single_library,
):
    """`is_unreleased=1` and `hidden=1` short-circuit the predicate —
    those books need different lifecycle steps before they can be
    scanned (release-date arrival or unhide)."""
    from app.discovery.sources.mam import _NEEDS_SCAN_BASIC_BARE

    db, aid = single_library
    await db.execute(
        "INSERT INTO books "
        "(title, author_id, mam_status, is_unreleased, hidden) VALUES "
        "('unreleased-not_found', ?, 'not_found', 1, 0),"
        "('hidden-possible', ?, 'possible', 0, 1),"
        "('eligible-not_found', ?, 'not_found', 0, 0)",
        (aid, aid, aid),
    )
    await db.commit()

    rows = await db.execute_fetchall(
        f"SELECT title FROM books WHERE {_NEEDS_SCAN_BASIC_BARE}"
    )
    titles = [r[0] for r in rows]
    assert titles == ["eligible-not_found"]


async def test_strict_predicate_keeps_url_guard_for_null_rows(
    single_library,
):
    """STRICT preserves its defensive `mam_url IS NULL` check for
    never-scanned rows (a NULL-status row with a stale mam_url is
    excluded), but rescannable possible/not_found rows bypass that
    guard because 'possible' rows legitimately have a mam_url set."""
    from app.discovery.sources.mam import _NEEDS_SCAN_STRICT_BARE

    db, aid = single_library
    await db.execute(
        "INSERT INTO books (title, author_id, mam_status, mam_url) VALUES "
        "('null-no-url', ?, NULL, NULL),"
        "('null-with-stale-url', ?, NULL, 'https://stale'),"
        "('possible-with-url', ?, 'possible', 'https://match'),"
        "('not_found-no-url', ?, 'not_found', NULL)",
        (aid, aid, aid, aid),
    )
    await db.commit()

    rows = await db.execute_fetchall(
        f"SELECT title FROM books WHERE {_NEEDS_SCAN_STRICT_BARE}"
    )
    titles = sorted(r[0] for r in rows)
    assert titles == ["not_found-no-url", "null-no-url", "possible-with-url"]


# ─── Feature 1: auto-release on expected_date ────────────────────


async def test_scheduler_clears_expired_unreleased(
    single_library, monkeypatch, tmp_path,
):
    """The `mam_scheduler_loop` per-library tick clears `is_unreleased=1`
    on rows whose `expected_date` has passed, so they're picked up by
    the same tick's scan eligibility query."""
    from app import state as app_state
    from app.discovery import scheduled_jobs

    db, aid = single_library
    # Seed: past-date book (should flip), today book (should flip),
    # future-date book (must NOT flip), no-date book (must NOT flip).
    await db.execute(
        "INSERT INTO books (title, author_id, owned, is_unreleased, "
        "expected_date) VALUES "
        "('past-book', ?, 0, 1, '2020-01-01'),"
        "('today-book', ?, 0, 1, date('now', 'localtime')),"
        "('future-book', ?, 0, 1, '2099-12-31'),"
        "('no-date-book', ?, 0, 1, NULL)",
        (aid, aid, aid, aid),
    )
    await db.commit()

    # Set up the loop's prerequisites: settings enable MAM, no other
    # scan running, no library sync in progress.
    settings_path = tmp_path / "settings.json"
    from app import config as app_config
    settings_path.write_text(json.dumps({
        **app_config.DEFAULT_SETTINGS,
        "mam_enabled": True,
        "mam_session_id": "tok",
        "mam_scanning_enabled": True,
        "mam_scan_interval_minutes": 1,
        "last_mam_validated_at": __import__("time").time(),
        "mam_validation_ok": True,
    }))
    monkeypatch.setattr(app_config, "SETTINGS_PATH", settings_path)
    app_config._settings_cache["data"] = None
    app_config._settings_cache["mtime"] = object()

    app_state._mam_scan_progress = {"running": False}
    app_state._library_sync_in_progress = False

    # Stub the scan batch so we don't talk to MAM. We only care about
    # whether the auto-release step ran before the count query.
    async def fake_scan_batch(*_a, **_kw):
        return {"scanned": 0, "found": 0, "possible": 0,
                "not_found": 0, "errors": 0, "error": None}

    monkeypatch.setattr(scheduled_jobs, "mam_scan_batch", fake_scan_batch)

    async def fake_token():
        return "tok"
    from app.discovery.routers import mam as mam_router
    monkeypatch.setattr(mam_router, "_get_mam_token", fake_token)

    # Skip the validate call (would hit real MAM).
    async def fake_validate(_token, _flag):
        return {"success": True, "message": "ok"}
    monkeypatch.setattr(scheduled_jobs, "mam_validate", fake_validate)

    # Drive ONE tick of the loop, then cancel. Replace asyncio.sleep
    # with an immediate yield so the 60s wait is skipped, and arrange
    # for the second sleep to raise CancelledError.
    sleep_count = {"n": 0}
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        sleep_count["n"] += 1
        if sleep_count["n"] >= 2:
            # After the loop has done one full pass, cancel it.
            raise asyncio.CancelledError()
        await real_sleep(0)

    monkeypatch.setattr(scheduled_jobs.asyncio, "sleep", fast_sleep)

    with pytest.raises(asyncio.CancelledError):
        await scheduled_jobs.mam_scheduler_loop()

    # Verify the auto-release UPDATE ran: past-book and today-book
    # should be cleared; future-book and no-date-book should still
    # be unreleased.
    rows = await db.execute_fetchall(
        "SELECT title, is_unreleased FROM books ORDER BY title"
    )
    state_by_title = {r[0]: r[1] for r in rows}
    assert state_by_title == {
        "future-book": 1,
        "no-date-book": 1,
        "past-book": 0,
        "today-book": 0,
    }
