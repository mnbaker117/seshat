"""
Send to Pipeline — hands a confirmed-match book off to the acquisition
pipeline.

When a book has a confirmed MAM match (mam_status="found"), the user
can send it to the pipeline for automatic download and processing.
Supports both single-book sends and bulk sends. Calls `inject_grab`
directly (no HTTP round-trip) since both domains live in the same
process.
"""
import json
import logging
import re

from fastapi import APIRouter, Body, HTTPException

from app import state
from app.config import load_settings
from app.database import get_db as get_pipeline_db
from app.discovery.database import get_db as get_discovery_db
from app.orchestrator.auto_train import train_author
from app.orchestrator.dispatch import inject_grab

logger = logging.getLogger("seshat.discovery")

router = APIRouter(prefix="/api/discovery", tags=["pipeline-send"])

_MAM_URL_RX = re.compile(r"/t/(\d+)")
_BARE_ID_RX = re.compile(r"^\d+$")


def _extract_torrent_id(url_or_id: str) -> str | None:
    s = url_or_id.strip()
    if _BARE_ID_RX.match(s):
        return s
    m = _MAM_URL_RX.search(s)
    return m.group(1) if m else None


@router.post("/send-to-pipeline")
async def send_to_pipeline(data: dict = Body(...)):
    """Send one or more books to the pipeline for download.

    Accepts a list of book IDs. Only books with mam_status="found"
    are sent — others are silently skipped.
    """
    book_ids = data.get("book_ids", [])
    if not book_ids:
        raise HTTPException(400, "No books specified")

    if state.dispatcher is None:
        raise HTTPException(503, "Pipeline dispatcher not initialized")

    db = await get_discovery_db()
    try:
        placeholders = ",".join("?" * len(book_ids))
        rows = await (await db.execute(
            f"SELECT b.id, b.title, b.mam_url, b.mam_status, b.mam_torrent_id, "
            f"b.mam_category, "
            f"b.source_url, b.isbn, b.series_id, b.series_index, b.cover_url, "
            f"b.description, b.page_count, "
            f"a.name as author_name, s.name as series_name "
            f"FROM books b "
            f"JOIN authors a ON b.author_id = a.id "
            f"LEFT JOIN series s ON b.series_id = s.id "
            f"WHERE b.id IN ({placeholders})",
            book_ids,
        )).fetchall()
    finally:
        await db.close()

    if not rows:
        raise HTTPException(404, "No books found for the given IDs")

    found_rows = [r for r in rows if r["mam_status"] == "found" and r["mam_torrent_id"]]
    skipped = len(rows) - len(found_rows)

    if not found_rows:
        return {
            "sent": 0,
            "skipped": skipped,
            "message": "No books with 'Found' MAM status to send",
        }

    submitted = 0
    failed = 0
    results = []

    for r in found_rows:
        tid = _extract_torrent_id(str(r["mam_torrent_id"]))
        if tid is None:
            results.append({"torrent_id": str(r["mam_torrent_id"]), "ok": False, "error": "bad torrent ID"})
            failed += 1
            continue

        author = r["author_name"] or ""

        # Auto-train the author in the pipeline's allow-list.
        if author:
            pdb = await get_pipeline_db()
            try:
                await train_author(pdb, author, source="discovery")
            except Exception:
                pass
            finally:
                await pdb.close()

        try:
            result = await inject_grab(
                state.dispatcher,
                torrent_id=tid,
                torrent_name=(r["title"] or "").strip(),
                category=(r["mam_category"] or "").strip(),
                author_blob=author,
                raw_line=f"discovery:{r['mam_torrent_id']}",
            )
            ok = result.action in ("submit", "queue") and result.error is None

            # Persist discovery metadata on the grab row for the enricher.
            if ok and result.grab_id:
                metadata = {}
                if r["isbn"]:
                    metadata["isbn"] = r["isbn"]
                if r["cover_url"]:
                    metadata["cover_url"] = r["cover_url"]
                if r["description"]:
                    metadata["description"] = r["description"]
                if r["page_count"]:
                    metadata["page_count"] = r["page_count"]
                if r["series_name"]:
                    metadata["series_name"] = r["series_name"]
                if r["series_index"]:
                    metadata["series_index"] = r["series_index"]
                if metadata:
                    try:
                        pdb = await get_pipeline_db()
                        try:
                            await pdb.execute(
                                "UPDATE grabs SET source_metadata = ? WHERE id = ?",
                                (json.dumps(metadata), result.grab_id),
                            )
                            await pdb.commit()
                        finally:
                            await pdb.close()
                    except Exception:
                        logger.warning("Failed to persist metadata for grab_id=%s", result.grab_id, exc_info=True)

            results.append({"torrent_id": tid, "ok": ok, "action": result.action, "error": result.error})
            if ok:
                submitted += 1
            else:
                failed += 1
        except Exception as e:
            results.append({"torrent_id": tid, "ok": False, "error": str(e)})
            failed += 1

    # Notification
    try:
        from app.discovery.notify import notify_pipeline_sent
        await notify_pipeline_sent(submitted, skipped)
    except Exception:
        pass

    logger.info(f"Send-to-pipeline: {submitted} submitted, {failed} failed, {skipped} skipped")

    return {
        "sent": submitted,
        "skipped": skipped,
        "failed": failed,
        "message": f"Sent {submitted} to pipeline" + (f", {skipped} skipped (not Found)" if skipped else ""),
        "results": results,
    }


@router.get("/pipeline/status")
async def pipeline_status():
    """Check if the pipeline dispatcher is initialized."""
    return {
        "configured": True,
        "reachable": state.dispatcher is not None,
        "internal": True,
    }
