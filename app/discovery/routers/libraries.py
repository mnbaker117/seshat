"""
Library discovery, switching, and validation endpoints.

Discovery supports multiple libraries (typically separate Calibre
installations) and exposes one as "active" at a time. The active
library determines which per-library SQLite database backs every
other endpoint, so switching libraries means tearing down any
in-flight scans cleanly first — otherwise a scan started against
library A would commit half its results to library B's database.

Endpoints:
  GET  /api/libraries                — list discovered libraries + active flag
  POST /api/libraries/active         — switch active library (cancels scans)
  POST /api/libraries/validate-path  — pre-flight check for setup wizard
  POST /api/libraries/rescan         — re-discover libraries from disk + env
"""
import logging
import os
from pathlib import Path
from fastapi import APIRouter, Body, HTTPException

from app.config import load_settings, save_settings, discover_libraries
from app.discovery.database import (
    get_db,
    init_db,
    set_active_library,
    get_active_library,
)
from app.library_apps import get_app
from app.discovery.sources.mam import cancel_full_scan as mam_cancel_full_scan
from app import state

logger = logging.getLogger("seshat.discovery")

router = APIRouter(prefix="/api/discovery", tags=["libraries"])


@router.get("/libraries")
async def list_libraries():
    """Return all discovered Calibre libraries with active flag."""
    active = get_active_library()
    return {
        "libraries": [
            {
                "name": lib["name"],
                "slug": lib["slug"],
                "app_type": lib.get("app_type", "calibre"),
                "content_type": lib.get("content_type", "ebook"),
                "display_name": lib.get("display_name", "Calibre"),
                "source_db_path": lib["source_db_path"],
                "library_path": lib["library_path"],
                "active": lib["slug"] == active,
            }
            for lib in state._discovered_libraries
        ]
    }


@router.post("/libraries/active")
async def switch_library(body: dict = Body(...)):
    """Switch the active library. Cancels any running scans first."""
    slug = body.get("slug", "")
    valid_slugs = [l["slug"] for l in state._discovered_libraries]
    if slug not in valid_slugs:
        raise HTTPException(400, f"Unknown library slug: {slug}. Valid: {valid_slugs}")

    old_slug = get_active_library()
    if slug == old_slug:
        return {"status": "ok", "active": slug, "message": "Already active"}

    # ── Cancel all running scans before switching ──
    cancelled = []

    # Cancel author lookup
    if state._lookup_task and not state._lookup_task.done():
        state._lookup_task.cancel()
        # Fully reinit so the previous library's checked/total/current_author
        # don't bleed into the new library's dashboard before a new scan starts.
        state._lookup_progress = {
            "running": False, "checked": 0, "total": 0, "current_author": "",
            "current_book": "",
            "new_books": 0, "status": "cancelled (library switch)", "type": "none",
        }
        cancelled.append("author scan")

    # Cancel MAM scan (manual or scheduled)
    if state._mam_scan_task and not state._mam_scan_task.done():
        state._mam_scan_task.cancel()
        state._mam_scan_progress.update({"running": False, "status": "cancelled (library switch)"})
        cancelled.append("MAM scan")

    # Cancel MAM full scan
    if state._mam_full_scan_task and not state._mam_full_scan_task.done():
        state._mam_full_scan_task.cancel()
        try:
            db = await get_db()
            try:
                await mam_cancel_full_scan(db)
            finally:
                await db.close()
        except Exception:
            pass
        cancelled.append("MAM full scan")

    if cancelled:
        logger.info(f"Cancelled running scans due to library switch ({old_slug} → {slug}): {', '.join(cancelled)}")

    # ── Switch the active library ──
    set_active_library(slug)
    s = load_settings()
    s["active_library"] = slug
    save_settings(s)
    logger.info(f"Switched active library to '{slug}'")
    return {"status": "ok", "active": slug, "cancelled": cancelled}


@router.post("/libraries/validate-path")
async def validate_library_path(body: dict = Body(...)):
    """Validate a filesystem path for use as a library source.

    SECURITY: This endpoint takes user-supplied filesystem paths and
    performs read-only filesystem operations on them. It is INTENTIONALLY
    a filesystem browser for the library setup wizard — that's the whole
    feature. Access is gated by AuthMiddleware (see app/main.py) so only
    authenticated admins can call it. The admin is trusted by definition
    in Seshat's threat model — see SECURITY.md.

    Defense in depth: the input sanitization below rejects obviously
    malformed paths (empty, excessively long, containing null bytes)
    before any filesystem call is made. CodeQL flags the os.path / Path
    calls below as "uncontrolled data used in path expression"; those
    findings are documented as intentional via the comments above each
    flagged site and the SECURITY.md threat model.

    Supports any registered library app type — uses the app's db_filename
    to look for the correct database file (e.g., metadata.db for Calibre).
    """
    path = body.get("path", "").strip()
    path_type = body.get("type", "root")
    app_type = body.get("app_type", "calibre")

    # ─── Input sanitization (defense in depth) ──────────────────
    if not path:
        return {"valid": False, "error": "No path provided"}
    if len(path) > 4096:
        return {"valid": False, "error": "Path is too long (max 4096 characters)"}
    if "\x00" in path:
        return {"valid": False, "error": "Path contains null bytes"}

    # codeql[py/path-injection] -- Intentional filesystem browser for the
    # library setup wizard. Endpoint is auth-gated; admin is trusted.
    # See validate_library_path() docstring and SECURITY.md.
    if not os.path.exists(path):
        return {"valid": False, "error": f"Path does not exist: {path}"}

    # Get the database filename for this app type
    app_instance = get_app(app_type)
    db_filename = app_instance.db_filename if app_instance else "metadata.db"

    def _safe_exists(pth: Path) -> bool:
        """Stat a path, treating PermissionError/OSError as 'not found'.

        Py 3.12+ made Path.exists() propagate permission errors. Since
        this endpoint runs under the non-root container user, unreadable
        subdirs must not crash the whole validate/rescan flow.

        Note on the path-injection suppression below: this helper is
        called from within the validate-path endpoint, which is the
        intentional filesystem browser. The path argument is the same
        admin-supplied value that the top of the function already
        validates and that we already documented in SECURITY.md as a
        deliberate design choice. CodeQL re-flags it on every fresh
        data-flow path, hence the inline suppression here in addition
        to the one further up.
        """
        try:
            # codeql[py/path-injection] -- See the docstring above and
            # SECURITY.md "validate-path endpoint" exception.
            return pth.exists()
        except (PermissionError, OSError):
            return False

    found = []
    if path_type == "root":
        root = Path(path)
        try:
            children = sorted(root.iterdir())
        except (PermissionError, OSError):
            # Log the full exception server-side (admin can grep
            # container logs); return a generic message to the user
            # so we don't leak filesystem-level error details — same
            # pattern as the MAM handlers in app/sources/mam.py.
            logger.exception(f"validate-path: cannot list directory at {path!r}")
            return {
                "valid": False,
                "error": "Cannot list directory (permission denied or unreadable)",
            }
        for child in children:
            # Skip hidden directories — .dbus, .cache, .Trash, etc are
            # never real libraries and are often unreadable as uid 1000.
            if child.name.startswith("."):
                continue
            try:
                if not child.is_dir():
                    continue
            except (PermissionError, OSError):
                continue
            db_file = child / db_filename
            if _safe_exists(db_file):
                found.append({"name": child.name, "path": str(db_file)})
        # codeql[py/path-injection] -- see validate_library_path() docstring
        root_db = root / db_filename
        if _safe_exists(root_db):
            found.append({"name": root.name, "path": str(root_db)})
        if not found:
            return {"valid": False, "error": f"No {db_filename} files found in subdirectories"}
        return {"valid": True, "libraries_found": len(found), "details": found}

    elif path_type == "direct":
        # codeql[py/path-injection] -- see validate_library_path() docstring
        p = Path(path)
        if p.name == db_filename and _safe_exists(p):
            return {"valid": True, "libraries_found": 1, "details": [{"name": p.parent.name, "path": str(p)}]}
        # codeql[py/path-injection] -- see validate_library_path() docstring
        elif _safe_exists(p / db_filename):
            return {"valid": True, "libraries_found": 1, "details": [{"name": p.name, "path": str(p / db_filename)}]}
        else:
            return {"valid": False, "error": f"No {db_filename} found at this path"}
    else:
        return {"valid": False, "error": f"Unknown type: {path_type}"}


@router.post("/libraries/rescan")
async def rescan_libraries():
    """Re-run library discovery from current settings. Initializes new databases."""
    s = load_settings()
    new_libs = discover_libraries(s)
    if not new_libs:
        return {"status": "error", "error": "No libraries found after rescan"}

    # Initialize any new library databases
    existing_slugs = {l["slug"] for l in state._discovered_libraries}
    for lib in new_libs:
        if lib["slug"] not in existing_slugs:
            await init_db(lib["slug"])
            logger.info(f"Initialized new library database: {lib['slug']}")

    state._discovered_libraries = new_libs
    lib_names = [f'"{l["name"]}" ({l["slug"]})' for l in new_libs]
    logger.info(f"Library rescan complete: {len(new_libs)} libraries found: {', '.join(lib_names)}")

    # Ensure active library is still valid
    active = get_active_library()
    valid_slugs = [l["slug"] for l in new_libs]
    if active not in valid_slugs:
        new_active = new_libs[0]["slug"]
        set_active_library(new_active)
        s["active_library"] = new_active
        save_settings(s)
        logger.info(f"Active library reset to '{new_active}' after rescan")

    return {
        "status": "ok",
        "libraries": [
            {"name": l["name"], "slug": l["slug"],
             "source_db_path": l["source_db_path"],
             "library_path": l["library_path"],
             "active": l["slug"] == get_active_library()}
            for l in new_libs
        ]
    }
