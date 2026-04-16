"""
AthenaScout integration endpoint.

    POST /api/v1/grabs/from-athenascout

Accepts a batch of MAM torrent URLs (or bare IDs) from AthenaScout's
"Send to Seshat" button. Each URL is parsed into a torrent_id and
routed through `inject_grab`, which handles the full
filter-skip → fetch → qBit pipeline.

Authors from the request are optionally auto-trained to the allow
list if they're not already present. This covers the case where
AthenaScout knows the author (because the user is scanning their
library) but Seshat doesn't (because it hasn't seen that author
in an IRC announce yet).

No MAM API key or special auth beyond the existing session cookie
middleware — AthenaScout and Seshat are both LAN services behind
the same auth boundary.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import state
from app.database import get_db
from app.orchestrator.auto_train import train_author
from app.orchestrator.dispatch import inject_grab

_log = logging.getLogger("seshat.routers.athenascout")

router = APIRouter(prefix="/api/v1/grabs", tags=["athenascout"])

_MAM_URL_RX = re.compile(r"/t/(\d+)")
_BARE_ID_RX = re.compile(r"^\d+$")


class GrabItem(BaseModel):
    url_or_id: str
    author: Optional[str] = None
    # Book title as AthenaScout knows it. Passed to `inject_grab`
    # as `torrent_name` so the grab row, dashboard, review queue
    # label, and enricher fuzzy-search all use the real title
    # instead of the `manual_inject_<id>` placeholder. Absent from
    # pre-v1.1.4 AthenaScout clients — in that case the placeholder
    # still lands on the row (backward-compatible fallback).
    title: Optional[str] = None
    # MAM category as AthenaScout saw it during its own MAM scan
    # (e.g. "Ebooks - Fantasy"). Lands on the grab row's `category`
    # field so the dashboard filter, budget-watcher category
    # reconciliation, and any cross-ref against the IRC announce
    # category gate all have the right value. Empty string from
    # pre-v1.1.5 AthenaScout clients — grab row then keeps the
    # existing empty-category fallback.
    category: Optional[str] = None
    # Optional pre-fetched metadata bundle from AthenaScout's source
    # scan. When present, Seshat stores the dict on the grab row
    # and skips its own enricher chain in _prepare_book — saves
    # 6 outbound scraper requests per book and guarantees metadata
    # consistency between the two apps. See plan item 1.2.
    #
    # Expected keys (all optional):
    #   goodreads_url, hardcover_url, kobo_url, amazon_url,
    #   isbn, cover_url, page_count, description,
    #   series_name, series_index
    # Unknown keys are stored as-is; the pipeline reads only the
    # keys it recognizes.
    metadata: Optional[dict[str, Any]] = None


class AthenascoutRequest(BaseModel):
    items: list[GrabItem] = Field(..., min_length=1, max_length=100)


class GrabResultItem(BaseModel):
    torrent_id: str
    ok: bool
    action: Optional[str] = None
    error: Optional[str] = None


class AthenascoutResponse(BaseModel):
    submitted: int
    failed: int
    results: list[GrabResultItem]


def _extract_torrent_id(url_or_id: str) -> Optional[str]:
    s = url_or_id.strip()
    if _BARE_ID_RX.match(s):
        return s
    m = _MAM_URL_RX.search(s)
    return m.group(1) if m else None


@router.post("/from-athenascout", response_model=AthenascoutResponse)
async def from_athenascout(body: AthenascoutRequest) -> AthenascoutResponse:
    if state.dispatcher is None:
        raise HTTPException(503, "dispatcher not initialized")

    results: list[GrabResultItem] = []
    submitted = 0
    failed = 0

    for item in body.items:
        tid = _extract_torrent_id(item.url_or_id)
        if tid is None:
            results.append(
                GrabResultItem(
                    torrent_id=item.url_or_id,
                    ok=False,
                    error=f"could not parse torrent ID from: {item.url_or_id}",
                )
            )
            failed += 1
            continue

        # Auto-train the author if provided and not already known.
        if item.author:
            db = await get_db()
            try:
                await train_author(db, item.author, source="athenascout")
            except Exception:
                pass
            finally:
                await db.close()

        try:
            result = await inject_grab(
                state.dispatcher,
                torrent_id=tid,
                torrent_name=(item.title or "").strip(),
                category=(item.category or "").strip(),
                author_blob=item.author or "",
                raw_line=f"athenascout:{item.url_or_id}",
            )
            ok = result.action in ("submit", "queue") and result.error is None

            # Persist the AthenaScout metadata bundle on the grab row
            # so _prepare_book can use it to skip the enricher later.
            # Best-effort: a JSON serialization or DB failure here must
            # not flip the grab's success — the torrent was accepted;
            # we just lose the short-circuit optimization.
            if ok and item.metadata and result.grab_id:
                try:
                    db = await get_db()
                    try:
                        await db.execute(
                            "UPDATE grabs SET source_metadata = ? WHERE id = ?",
                            (json.dumps(item.metadata), result.grab_id),
                        )
                        await db.commit()
                    finally:
                        await db.close()
                except Exception:
                    _log.warning(
                        "athenascout: failed to persist metadata for grab_id=%s (non-fatal)",
                        result.grab_id, exc_info=True,
                    )

            results.append(
                GrabResultItem(
                    torrent_id=tid,
                    ok=ok,
                    action=result.action,
                    error=result.error,
                )
            )
            if ok:
                submitted += 1
            else:
                failed += 1
        except Exception as e:
            results.append(
                GrabResultItem(
                    torrent_id=tid,
                    ok=False,
                    error=str(e),
                )
            )
            failed += 1

    _log.info(
        "athenascout batch: %d submitted, %d failed out of %d",
        submitted, failed, len(body.items),
    )
    return AthenascoutResponse(
        submitted=submitted, failed=failed, results=results
    )
