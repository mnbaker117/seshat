"""
Scan-orchestration endpoints.

Three scan kinds run through this router:
  - Library sync: imports the user's curated library (Calibre today,
    other backends in the future) into AthenaScout's DB.
  - Author/source lookup: hits Goodreads/Hardcover/Kobo for each author.
  - Full re-scan: same as lookup but visits every book page to refresh
    metadata, ignoring the cache window.

Plus the unified `/scan-status` endpoint that the Dashboard polls so it
can render every active scan side-by-side, regardless of which router
actually started the scan. The lookup-specific and MAM-specific status
endpoints still exist (consumed by the MAMPage and SettingsPage), so
this file *projects* the underlying state dicts into a uniform shape
rather than restructuring them.

Endpoints:
  /api/sync/library, /api/sync, /api/sync/calibre     — manual library sync
  /api/sync/lookup, /api/lookup                       — start author scan
  /api/lookup/cancel, /api/lookup/status              — control + status
  /api/sync/full-rescan                               — full re-scan
  /api/scan-status                                    — unified Dashboard feed
  /api/scanning/{author,mam}/toggle                   — feature on/off
"""
import asyncio
import logging
import os
import time
from fastapi import APIRouter, HTTPException

from app.discovery.calibre_sync import sync_calibre
from app.config import load_settings, save_settings
from app.discovery.database import get_active_library, get_db
from app.library_apps import get_app
from app.discovery.lookup import run_full_lookup, run_full_rescan
from app import state

logger = logging.getLogger("seshat.discovery")

router = APIRouter(prefix="/api/discovery", tags=["scan"])


# ─── Library Sync ────────────────────────────────────────────
@router.post("/sync/library")
async def trigger_sync():
    """Manual sync of the active library.

    Routes through the active library backend's `sync()` method via
    its `LibraryApp` adapter. Flagged through
    `state._library_sync_in_progress` so MAM and other write-heavy
    background tasks yield to us cleanly.
    """
    active_slug = get_active_library()
    lib = next((l for l in state._discovered_libraries if l["slug"] == active_slug), None)
    # Flag the sync so background writers (MAM scanner) yield to us
    # instead of racing on the write lock. Always cleared in finally.
    state._library_sync_in_progress = True
    try:
        if lib:
            app_instance = get_app(lib.get("app_type", "calibre"))
            if app_instance:
                result = await app_instance.sync(lib["source_db_path"], lib["library_path"])
            else:
                result = await sync_calibre(lib["source_db_path"], lib["library_path"])
            # Update mtime after successful manual sync
            s = load_settings()
            mtimes = s.get("library_mtimes", {})
            mtimes[active_slug] = os.path.getmtime(lib["source_db_path"])
            s["library_mtimes"] = mtimes
            save_settings(s)
            try:
                from app.discovery.notify import notify_library_sync
                await notify_library_sync(
                    lib.get("display_name") or lib.get("name") or "Library",
                    int((result or {}).get("books_new", 0)),
                    int((result or {}).get("books_updated", 0)),
                )
            except Exception:
                logger.debug("library-sync notify failed", exc_info=True)
        else:
            result = await sync_calibre()
        state._last_library_sync_check["at"] = time.time()
        state._last_library_sync_check["synced"] = True
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        state._library_sync_in_progress = False


# Back-compat aliases. /sync/calibre is what the original public release
# documented; /sync is the legacy short alias. Both still hit the same
# handler so any older client / saved bookmark keeps working.
@router.post("/sync/calibre")
async def trigger_sync_calibre_alias():
    return await trigger_sync()


@router.post("/sync")
async def trigger_sync_alias():
    return await trigger_sync()


# ─── Author Lookup ───────────────────────────────────────────
@router.post("/sync/lookup")
async def trigger_lookup():
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    if state._lookup_task and not state._lookup_task.done():
        return {"error": "An author scan is already running"}

    # Pre-flight: how many authors are actually due for scanning given the
    # cache window? If zero, surface a clear "nothing to do" status instead
    # of starting a no-op task that briefly shows "Scanning... 0 of 0" then
    # vanishes. Users hitting this most often have just completed a scan and
    # are inside the lookup_interval_days cache window.
    cache_sec = s.get("lookup_interval_days", 3) * 86400
    cutoff = time.time() - cache_sec
    db = await get_db()
    try:
        # Match what run_full_lookup() actually iterates: skip orphan
        # authors so the "due count" estimate isn't inflated by authors
        # that the lookup loop will silently filter out.
        row = await (await db.execute(
            "SELECT COUNT(*) c FROM authors WHERE COALESCE(last_lookup_at,0) < ? AND id IN (SELECT DISTINCT author_id FROM books)",
            (cutoff,),
        )).fetchone()
        due_count = row["c"] if row else 0
    finally:
        await db.close()
    if due_count == 0:
        state._lookup_progress = {
            "running": False, "checked": 0, "total": 0, "current_author": "",
            "current_book": "",
            "new_books": 0, "type": "lookup",
            "status": f"no authors due (cache window: {s.get('lookup_interval_days', 3)} days)",
        }
        return {"status": "ok", "due": 0,
                "message": "No authors due for scanning within the current cache window."}

    state._lookup_progress = {"running": True, "checked": 0, "total": due_count, "current_author": "",
                        "current_book": "",
                        "new_books": 0, "status": "scanning", "type": "lookup"}
    def _progress(data):
        state._lookup_progress.update({"checked": data["checked"], "total": data["total"],
                                 "current_author": data["current_author"], "new_books": data["new_books"]})
    async def _do():
        try:
            result = await run_full_lookup(on_progress=_progress)
            state._lookup_progress.update({
                "running": False, "status": "complete",
                "source_timeouts": result.get("source_timeouts") or {},
            })
            try:
                from app.discovery.notify import notify_scan_complete
                await notify_scan_complete(
                    label="Source Scan",
                    new_books=int(state._lookup_progress.get("new_books", 0)),
                    authors_total=int(state._lookup_progress.get("total", 0) or 1),
                )
            except Exception:
                logger.debug("source-scan notify failed", exc_info=True)
        except Exception as e:
            logger.error(f"Author scan error: {e}")
            state._lookup_progress.update({"running": False, "status": f"error: {e}"})
    state._lookup_task = asyncio.create_task(_do())
    return {"status": "started"}


@router.post("/lookup")
async def trigger_lookup_alias():
    return await trigger_lookup()


@router.post("/lookup/cancel")
async def lookup_cancel():
    """Cancel the currently running author scan."""
    if state._lookup_task and not state._lookup_task.done():
        state._lookup_task.cancel()
        state._lookup_progress.update({"running": False, "status": "cancelled"})
        logger.info("Author scan cancelled by user")
        return {"status": "ok", "message": "Author scan cancelled"}
    return {"status": "ok", "message": "No author scan running"}


@router.get("/lookup/status")
async def lookup_status():
    """Get progress of the current/most recent author scan."""
    return dict(state._lookup_progress)


# ─── Unified scan status ─────────────────────────────────────
# Each kind of scan stores its progress in a different state dict with
# different field names (`checked`/`total`, `scanned`/`total`, etc.).
# The `_project_*` helpers below normalize them into a uniform shape:
#
#   { kind, type, label, running, current, total,
#     current_label, current_book, status, extra }
#
# The frontend maps over the resulting `scans` array and renders one row
# per active scan, so multiple scans can show side-by-side when MAM,
# author lookup, and Calibre sync are all running concurrently.
def _label_for(kind: str, scan_type: str) -> str:
    """Human-readable label for a (kind, type) pair."""
    if kind == "lookup":
        return {
            "lookup":             "Source Scan",
            "full_rescan":        "Full Re-Scan",
            "scheduled_lookup":   "Scheduled Source Scan",
            "single_author":      "Author Scan",
            "single_author_full": "Author Full Re-Scan",
            "bulk_authors":       "Bulk Author Scan",
            "bulk_books":         "Bulk Book Scan",
        }.get(scan_type, "Source Scan")
    if kind == "mam":
        return {
            "manual":    "MAM Scan",
            "scheduled": "Scheduled MAM Scan",
            "full_scan": "MAM Full Scan",
        }.get(scan_type, "MAM Scan")
    if kind == "library":
        # When a non-Calibre backend lands, the projection passes
        # `display_name` through here as `scan_type`, so the label
        # reads "Audiobookshelf Sync" naturally instead of always
        # saying "Calibre Sync".
        return f"{scan_type} Sync" if scan_type and scan_type != "none" else "Library Sync"
    return scan_type or kind


def _stamp_completed(p: dict) -> float | None:
    """Lazily stamp completed_at when a scan first appears as not-running.

    Returns the timestamp if the scan has finished, None if still running
    or never ran. The stamp is written back into the progress dict so
    subsequent calls return the same value.
    """
    running = bool(p.get("running"))
    status = p.get("status", "idle")
    if running:
        p.pop("completed_at", None)
        return None
    if status in ("idle", "none"):
        return None
    if "completed_at" not in p:
        p["completed_at"] = time.time()
    return p["completed_at"]


def _project_lookup() -> dict:
    """Project _lookup_progress into the unified shape."""
    p = state._lookup_progress
    return {
        "kind": "lookup",
        "type": p.get("type", "none"),
        "label": _label_for("lookup", p.get("type", "none")),
        "running": bool(p.get("running")),
        "current": p.get("checked", 0),
        "total": p.get("total", 0),
        "current_label": p.get("current_author", "") or None,
        # In-flight book title the source scan is currently fetching.
        # Goodreads/Kobo/Hardcover write to this via the `_on_book`
        # closure that lookup.py stashes on each source instance, and
        # only for work that actually does something — DETAIL fetches
        # and URL-backfill matches. Filter-noise skips don't reach
        # this field, so the user-visible feed never flickers through
        # foreign-language / set-collection / contributor-only noise.
        "current_book": p.get("current_book", "") or None,
        "status": p.get("status", "idle"),
        "completed_at": _stamp_completed(p),
        "extra": {
            "new_books": p.get("new_books", 0),
            # Per-source author-timeout counts from the most recent bulk
            # scan. Dashboard surfaces this so a primary source (Goodreads)
            # silently under-scanning a batch of authors doesn't get lost
            # in the logs. Empty dict when nothing timed out.
            "source_timeouts": p.get("source_timeouts") or {},
        },
    }


def _project_mam() -> dict:
    """Project _mam_scan_progress into the unified shape."""
    p = state._mam_scan_progress
    return {
        "kind": "mam",
        "type": p.get("type", "none"),
        "label": _label_for("mam", p.get("type", "none")),
        "running": bool(p.get("running")),
        "current": p.get("scanned", 0),
        "total": p.get("total", 0),
        "current_label": None,
        # In-flight book MAM is currently checking. Unlike source scans,
        # MAM shows EVERY attempt — there's no filter-noise to hide here.
        "current_book": p.get("current_book", "") or None,
        "status": p.get("status", "idle"),
        "completed_at": _stamp_completed(p),
        "extra": {
            "found":     p.get("found", 0),
            "possible":  p.get("possible", 0),
            "not_found": p.get("not_found", 0),
            "errors":    p.get("errors", 0),
            "remaining": p.get("remaining"),
        },
    }


def _project_library() -> dict:
    """Project `_library_sync_progress` into the unified shape.

    Library sync is the third "kind" in the unified scan widget,
    alongside `lookup` and `mam`. This lets the user see exactly how
    far a sync has gotten before kicking off another scan that would
    block behind it (the `_library_sync_in_progress` flag still gates
    writers — see the active backend's `sync()` method and the MAM
    scanner's wait-for-other-writers loop).

    The `label` here passes the active library's `display_name`
    through `_label_for` so a Calibre sync renders as "Calibre Sync"
    today and a future Audiobookshelf sync would render as
    "Audiobookshelf Sync" automatically — no projection changes
    required when a new backend lands.
    """
    p = state._library_sync_progress
    # Look up the active library's display_name for the label.
    active_slug = get_active_library()
    lib = next(
        (l for l in state._discovered_libraries if l["slug"] == active_slug),
        None,
    )
    display_name = (lib or {}).get("display_name") or "Library"
    return {
        "kind": "library",
        "type": p.get("type", "none"),
        "label": _label_for("library", display_name),
        "running": bool(p.get("running")),
        "current": p.get("current", 0),
        "total": p.get("total", 0),
        "current_label": None,
        "current_book": p.get("current_book", "") or None,
        "status": p.get("status", "idle"),
        "completed_at": _stamp_completed(p),
        "extra": {
            "books_new": p.get("books_new", 0),
            "books_updated": p.get("books_updated", 0),
        },
    }


@router.get("/scan-status")
async def scan_status():
    """Unified scan progress for the Dashboard widget.

    Returns every tracked scan in a uniform shape regardless of whether
    it's an author lookup, full re-scan, MAM scan, scheduled job, or a
    single-author trigger from the Author page. The frontend renders
    one row per scan with running > complete > idle ordering. A scan
    in 'idle' state with type='none' is filtered out so the widget
    auto-hides when nothing has run yet.
    """
    out = []
    for proj in (_project_lookup(), _project_mam(), _project_library()):
        # Hide entries that are pristine idle (never ran). Keep complete
        # ones so the user sees the result of the last scan even after
        # it finishes.
        if proj["status"] == "idle" and proj["type"] == "none":
            continue
        out.append(proj)
    return {"scans": out}


@router.post("/sync/full-rescan")
async def trigger_full_rescan():
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    if state._lookup_task and not state._lookup_task.done():
        return {"error": "An author scan is already running"}
    state._lookup_progress = {"running": True, "checked": 0, "total": 0, "current_author": "",
                        "current_book": "",
                        "new_books": 0, "status": "scanning", "type": "full_rescan"}
    def _progress(data):
        state._lookup_progress.update({"checked": data["checked"], "total": data["total"],
                                 "current_author": data["current_author"], "new_books": data["new_books"]})
    async def _do():
        try:
            result = await run_full_rescan(on_progress=_progress)
            state._lookup_progress.update({
                "running": False, "status": "complete",
                "source_timeouts": result.get("source_timeouts") or {},
            })
            try:
                from app.discovery.notify import notify_scan_complete
                await notify_scan_complete(
                    label="Full Re-Scan",
                    new_books=int(state._lookup_progress.get("new_books", 0)),
                    authors_total=int(state._lookup_progress.get("total", 0) or 1),
                )
            except Exception:
                logger.debug("full-rescan notify failed", exc_info=True)
        except Exception as e:
            logger.error(f"Full re-scan error: {e}")
            state._lookup_progress.update({"running": False, "status": f"error: {e}"})
    state._lookup_task = asyncio.create_task(_do())
    return {"status": "started"}


# ─── Scanning Toggles ────────────────────────────────────────
@router.post("/scanning/author/toggle")
async def toggle_author_scanning():
    """Toggle author scanning on/off. Cancels running scan when disabled."""
    s = load_settings()
    new_val = not s.get("author_scanning_enabled", True)
    s["author_scanning_enabled"] = new_val
    save_settings(s)
    if not new_val and state._lookup_task and not state._lookup_task.done():
        state._lookup_task.cancel()
        state._lookup_progress.update({"running": False, "status": "cancelled"})
        logger.info("Author scanning disabled — cancelled running scan")
    return {"enabled": new_val}


@router.post("/scanning/mam/toggle")
async def toggle_mam_scanning():
    """Toggle MAM scanning on/off without affecting MAM feature visibility."""
    s = load_settings()
    new_val = not s.get("mam_scanning_enabled", True)
    s["mam_scanning_enabled"] = new_val
    save_settings(s)
    if not new_val:
        if state._mam_scan_task and not state._mam_scan_task.done():
            state._mam_scan_task.cancel()
            state._mam_scan_progress.update({"running": False, "status": "cancelled"})
        if state._mam_full_scan_task and not state._mam_full_scan_task.done():
            state._mam_full_scan_task.cancel()
            state._mam_scan_progress.update({"running": False, "status": "cancelled"})
        logger.info("MAM scanning disabled — cancelled running scans")
    return {"enabled": new_val}
