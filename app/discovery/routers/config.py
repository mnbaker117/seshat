"""
App-level config and status endpoints.

  GET  /api/settings        — current saved settings (DEFAULT_SETTINGS
                              merged with user overrides)
  POST /api/settings        — partial update; merged into settings.json
  POST /api/settings/reset  — wipe back to defaults
  GET  /api/health          — liveness probe (public, no auth)
  GET  /api/platform        — runtime mode + OS info for the setup wizard
  GET  /api/stats           — Dashboard stats (counts, last sync time, etc.)
"""
import logging
import time
from pathlib import Path
from fastapi import APIRouter, Body

from app.config import (
    LANGUAGE_OPTIONS,
    load_settings,
    save_settings,
    apply_logging,
    get_extra_mount_paths,
)
from app.discovery.database import get_db, get_active_library, HF
from app.discovery.lookup import reload_sources
from app.discovery.sources.mam import get_mam_stats
from app import state

logger = logging.getLogger("seshat.discovery")

router = APIRouter(prefix="/api/discovery", tags=["config"])


# ─── Settings ────────────────────────────────────────────────
@router.get("/settings")
async def get_settings():
    from app.secrets import get_secret, SECRET_KEYS
    s = load_settings()
    d = dict(s)
    # Check the encrypted store for secret keys — if present there,
    # the settings.json value is stale/blank (migration already ran).
    for key in SECRET_KEYS:
        encrypted_val = await get_secret(key)
        if encrypted_val:
            d[f"{key}_set"] = True
            d[key] = encrypted_val[:8] + "..." if len(encrypted_val) > 8 else "***"
        elif d.get(key):
            # Fallback: still in settings.json (pre-migration)
            d[f"{key}_set"] = True
            d[key] = d[key][:8] + "..."
        else:
            d[f"{key}_set"] = False
    d["language_options"] = LANGUAGE_OPTIONS
    d["_extra_mount_paths"] = get_extra_mount_paths()
    d["_discovered_libraries"] = [
        {"name": l["name"], "slug": l["slug"],
         "app_type": l.get("app_type", "calibre"),
         "content_type": l.get("content_type", "ebook"),
         "source_db_path": l["source_db_path"],
         "active": l["slug"] == get_active_library()}
        for l in state._discovered_libraries
    ]
    return d


@router.post("/settings")
async def update_settings(body: dict = Body(...)):
    from app.secrets import set_secret, SECRET_KEYS
    cur = load_settings()
    # Detect a Google Books re-enable so the circuit-breaker timestamp
    # can be cleared. Without this, re-enabling after a trip would keep
    # the Dashboard banner up forever (and the next scan would immediately
    # re-trip because the source instance gets rebuilt but the timestamp
    # would still show "auto-disabled at T").
    gb_reenabled = (
        body.get("google_books_enabled") is True
        and not cur.get("google_books_enabled")
    )
    for k, v in body.items():
        if k not in cur:
            continue
        # Route secret keys through the encrypted store
        if k in SECRET_KEYS:
            if isinstance(v, str) and ("..." in v or v == "***" or v == ""):
                continue  # masked/truncated value — don't overwrite
            await set_secret(k, v)
            cur[k] = ""  # blank in settings.json
            continue
        cur[k] = v
    if gb_reenabled:
        cur["google_books_auto_disabled_at"] = None
    save_settings(cur)
    reload_sources()
    apply_logging(cur.get("verbose_logging", False))
    return {"status": "ok"}


@router.post("/settings/reset")
async def reset_settings():
    """Reset all settings to factory defaults."""
    from app.config import DEFAULT_SETTINGS
    fresh = dict(DEFAULT_SETTINGS)
    save_settings(fresh)
    reload_sources()
    apply_logging(False)
    logger.info("All settings reset to defaults")
    return {"status": "ok"}


# ─── Health & Stats ──────────────────────────────────────────
@router.get("/health")
async def health():
    return {"status": "ok", "time": time.time()}


@router.get("/version")
async def version_info():
    """Return the build version (git SHA) baked into the Docker image."""
    from pathlib import Path
    version_file = Path("/app/VERSION")
    sha = version_file.read_text().strip() if version_file.exists() else "dev"
    return {"sha": sha, "short_sha": sha[:7] if len(sha) > 7 else sha}


@router.get("/platform")
async def platform_info():
    """Return platform/runtime info for the frontend.

    Used by the setup wizard to detect first-run state, suggest
    library paths, and adapt UI to the runtime environment.
    """
    from app.runtime import get_platform_info
    info = get_platform_info()
    s = load_settings()
    # First run: no libraries discovered AND no user-configured sources AND setup not completed
    info["first_run"] = (
        not state._discovered_libraries
        and not s.get("library_sources")
        and not s.get("setup_complete")
    )
    # Check which suggested default paths actually exist on this system
    info["existing_default_paths"] = [
        p for p in info["default_library_paths"]
        if Path(p["path"]).exists()
    ]
    return info


@router.get("/stats")
async def get_stats():
    db = await get_db()
    try:
        g = lambda sql: db.execute(sql)
        # Match the Authors page browse view (routers/authors.py:get_authors)
        # by excluding orphans. Otherwise the Dashboard's author count is
        # higher than the Authors page list and looks like a bug to users.
        authors = (await (await g("SELECT COUNT(*) c FROM authors WHERE id IN (SELECT DISTINCT author_id FROM books)")).fetchone())["c"]
        total = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE {HF}")).fetchone())["c"]
        owned = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE owned=1 AND {HF}")).fetchone())["c"]
        missing = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE owned=0 AND {HF}")).fetchone())["c"]
        new = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE is_new=1 AND owned=0 AND {HF}")).fetchone())["c"]
        upcoming = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE is_unreleased=1 AND owned=0 AND {HF}")).fetchone())["c"]
        series = (await (await g("SELECT COUNT(*) c FROM series")).fetchone())["c"]
        hidden = (await (await g("SELECT COUNT(*) c FROM books WHERE hidden=1")).fetchone())["c"]
        # Pull the most recent library-sync row from sync_log. The set
        # of "library sync" types is whatever's currently registered in
        # the library_apps registry, so this query stays correct as new
        # backends land — no future code change needed.
        from app.library_apps import get_all_apps
        lib_types = list(get_all_apps().keys())
        if lib_types:
            placeholders = ",".join("?" * len(lib_types))
            ls = await (await db.execute(
                f"SELECT * FROM sync_log WHERE sync_type IN ({placeholders}) "
                f"ORDER BY started_at DESC LIMIT 1",
                lib_types,
            )).fetchone()
        else:
            ls = None
        ll = await (await g("SELECT * FROM sync_log WHERE sync_type='lookup' ORDER BY started_at DESC LIMIT 1")).fetchone()
        s = load_settings()
        mam_stats = None
        if s.get("mam_enabled") and s.get("mam_session_id"):
            mam_stats = await get_mam_stats(db)
        active_lib = get_active_library()
        lib_info = next((l for l in state._discovered_libraries if l["slug"] == active_lib), None)
        return {"authors": authors, "total_books": total, "owned_books": owned, "missing_books": missing, "new_books": new, "upcoming_books": upcoming, "total_series": series, "hidden_books": hidden, "last_library_sync": dict(ls) if ls else None, "last_lookup": dict(ll) if ll else None, "calibre_web_url": s.get("calibre_web_url", ""), "calibre_url": s.get("calibre_url", ""), "mam": mam_stats, "mam_enabled": s.get("mam_enabled", False), "mam_scanning_enabled": s.get("mam_scanning_enabled", True), "author_scanning_enabled": s.get("author_scanning_enabled", True), "active_library": active_lib, "active_library_name": lib_info["name"] if lib_info else active_lib, "library_count": len(state._discovered_libraries), "active_content_type": lib_info.get("content_type", "ebook") if lib_info else "ebook", "active_app_type": lib_info.get("app_type", "calibre") if lib_info else "calibre", "last_library_sync_check": state._last_library_sync_check}
    finally:
        await db.close()
