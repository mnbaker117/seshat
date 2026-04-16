"""
Data management endpoints — safe and dangerous reset operations.

    POST /api/v1/data/clear/{target}     — clear a specific data store
    POST /api/v1/data/factory-reset      — nuclear option, clears everything

Each clear operation is scoped to a single table or concept. The
factory reset clears all pipeline data but preserves auth credentials
and the encrypted secret store.

Dangerous operations require a `confirm` field in the request body
set to the exact target name (e.g. {"confirm": "authors_allowed"}).
This prevents accidental clicks from wiping data.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.database import get_db

_log = logging.getLogger("seshat.routers.data_management")

router = APIRouter(prefix="/api/v1/data", tags=["data_management"])


# Safe targets: clearing these doesn't lose anything the user can't
# re-acquire from the next IRC session / MAM scan.
_SAFE_TARGETS = {
    "tentative_torrents": "DELETE FROM tentative_torrents",
    "book_review_queue": "DELETE FROM book_review_queue WHERE status = 'pending'",
    "ignored_torrents_seen": "DELETE FROM ignored_torrents_seen",
    "announces": "DELETE FROM announces",
    "authors_tentative_review": "DELETE FROM authors_tentative_review",
    "calibre_additions": "DELETE FROM calibre_additions",
}

# Dangerous targets: require typed confirmation.
_DANGEROUS_TARGETS = {
    "authors_allowed": "DELETE FROM authors_allowed",
    "authors_ignored": "DELETE FROM authors_ignored",
    "grabs": "DELETE FROM grabs",
    "pipeline_runs": "DELETE FROM pipeline_runs",
    "snatch_ledger": "DELETE FROM snatch_ledger",
    "pending_queue": "DELETE FROM pending_queue",
}


class ClearRequest(BaseModel):
    confirm: str = ""


class ClearResponse(BaseModel):
    ok: bool
    target: str
    rows_deleted: int


class FactoryResetResponse(BaseModel):
    ok: bool
    targets_cleared: list[str]


@router.post("/clear/{target}", response_model=ClearResponse)
async def clear_target(target: str, body: ClearRequest = Body(default=ClearRequest())) -> ClearResponse:
    """Clear a specific data store.

    Safe targets clear without confirmation. Dangerous targets
    require body.confirm == target name.
    """
    sql = _SAFE_TARGETS.get(target)
    is_dangerous = False

    if sql is None:
        sql = _DANGEROUS_TARGETS.get(target)
        is_dangerous = True

    if sql is None:
        raise HTTPException(400, f"Unknown target: {target}")

    if is_dangerous and body.confirm != target:
        raise HTTPException(
            400,
            f"Dangerous operation — set confirm to '{target}' to proceed",
        )

    db = await get_db()
    try:
        cursor = await db.execute(sql)
        await db.commit()
        deleted = cursor.rowcount
    finally:
        await db.close()

    _log.info("data clear: %s — %d rows deleted", target, deleted)
    return ClearResponse(ok=True, target=target, rows_deleted=deleted)


@router.post("/factory-reset", response_model=FactoryResetResponse)
async def factory_reset(body: ClearRequest = Body(...)) -> FactoryResetResponse:
    """Nuclear option: clear ALL pipeline data.

    Preserves: auth credentials, encrypted secrets, settings.json.
    Requires body.confirm == "FACTORY_RESET".
    """
    if body.confirm != "FACTORY_RESET":
        raise HTTPException(
            400,
            "Set confirm to 'FACTORY_RESET' to proceed — this is irreversible",
        )

    all_targets = {**_SAFE_TARGETS, **_DANGEROUS_TARGETS}
    cleared: list[str] = []

    db = await get_db()
    try:
        for target, sql in all_targets.items():
            try:
                await db.execute(sql)
                cleared.append(target)
            except Exception:
                _log.exception("factory reset: failed to clear %s", target)
        await db.commit()
    finally:
        await db.close()

    _log.warning("FACTORY RESET executed — cleared %d targets", len(cleared))
    return FactoryResetResponse(ok=True, targets_cleared=cleared)


@router.get("/counts")
async def get_counts():
    """Row counts for the data management UI."""
    db = await get_db()
    try:
        counts = {}
        for table in [
            "tentative_torrents", "book_review_queue", "ignored_torrents_seen",
            "announces", "authors_tentative_review", "calibre_additions",
            "authors_allowed", "authors_ignored", "grabs", "pipeline_runs",
            "snatch_ledger", "pending_queue",
        ]:
            try:
                cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
                row = await cursor.fetchone()
                counts[table] = int(row[0]) if row else 0
            except Exception:
                counts[table] = -1
        return counts
    finally:
        await db.close()
