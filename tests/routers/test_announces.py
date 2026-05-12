"""
HTTP-level tests for the v2.9.0 announces audit log endpoint.

Exercises GET /api/v1/announces against a temp SQLite DB seeded with
a representative mix of decisions (allow / skip / hold) and dedup
reasons. Verifies:

  - Default fetch returns newest-first with no filters applied.
  - decision filter (single + multi-value) narrows correctly.
  - reason substring filter narrows correctly.
  - q (full-text) filter narrows across torrent_name / author / category.
  - decision_counts reflects pre-filter totals (so chips show
    "what would be visible if I clicked this") but honors q/reason.
  - limit cap is respected.
  - empty table returns empty rows + zero counts.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.database import get_db
from app.routers.announces import router as announces_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(announces_router)
    return app


async def _seed(rows: list[dict]) -> None:
    """Insert rows into the announces table. Each dict needs
    `torrent_name`, `author_blob`, `category`, `decision`,
    `decision_reason`, and optionally `filetype` + `matched_author`.
    """
    db = await get_db()
    try:
        for r in rows:
            await db.execute(
                """
                INSERT INTO announces
                  (raw, torrent_id, torrent_name, category, author_blob,
                   decision, decision_reason, matched_author, filetype)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.get("raw", ""),
                    r.get("torrent_id", "1234"),
                    r["torrent_name"],
                    r["category"],
                    r["author_blob"],
                    r["decision"],
                    r["decision_reason"],
                    r.get("matched_author", ""),
                    r.get("filetype"),
                ),
            )
        await db.commit()
    finally:
        await db.close()


@pytest.fixture
async def seeded_db(temp_db):
    """A temp DB pre-loaded with a representative mix mirroring the
    Keleros incident plus a couple of holds for v2.9.0 coverage."""
    await _seed([
        # The Delves: AZW3 held, then EPUB preempts.
        {
            "torrent_name": "The Delves",
            "author_blob": "Keleros",
            "category": "Ebooks - Fantasy",
            "decision": "hold",
            "decision_reason": "format_dedup_hold",
            "filetype": "azw3",
        },
        {
            "torrent_name": "The Delves",
            "author_blob": "Keleros",
            "category": "Ebooks - Fantasy",
            "decision": "allow",
            "decision_reason": "format_dedup_enabled_grab",
            "filetype": "epub",
        },
        # The Duchy: EPUB grabs, AZW3 skipped.
        {
            "torrent_name": "The Duchy",
            "author_blob": "Keleros",
            "category": "Ebooks - Fantasy",
            "decision": "allow",
            "decision_reason": "allowed_author",
            "filetype": "epub",
        },
        {
            "torrent_name": "The Duchy",
            "author_blob": "Keleros",
            "category": "Ebooks - Fantasy",
            "decision": "skip",
            "decision_reason": "format_dedup_higher_priority_inflight",
            "filetype": "azw3",
        },
        # Unrelated skip on a non-dedup reason.
        {
            "torrent_name": "Some Romance",
            "author_blob": "Unknown Person",
            "category": "Ebooks - Romance",
            "decision": "skip",
            "decision_reason": "author_not_allowlisted",
            "filetype": "epub",
        },
    ])
    yield


@pytest.fixture
async def client(seeded_db):
    """ASGI client targeting the announces router."""
    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
    ) as ac:
        yield ac


class TestAnnouncesEndpoint:
    async def test_default_returns_newest_first(self, client):
        r = await client.get("/api/v1/announces")
        assert r.status_code == 200
        body = r.json()
        assert len(body["rows"]) == 5
        # Newest-first by id desc means the last-seeded row leads.
        assert body["rows"][0]["torrent_name"] == "Some Romance"
        assert body["total_matched"] == 5

    async def test_decision_counts_present(self, client):
        r = await client.get("/api/v1/announces")
        counts = r.json()["decision_counts"]
        assert counts == {"allow": 2, "skip": 2, "hold": 1}

    async def test_filter_by_single_decision(self, client):
        r = await client.get("/api/v1/announces?decision=hold")
        body = r.json()
        assert body["total_matched"] == 1
        assert all(row["decision"] == "hold" for row in body["rows"])
        # decision_counts ignores the decision filter — chip UX.
        assert body["decision_counts"]["allow"] == 2

    async def test_filter_by_multiple_decisions(self, client):
        r = await client.get("/api/v1/announces?decision=hold,skip")
        body = r.json()
        assert body["total_matched"] == 3
        assert all(row["decision"] in {"hold", "skip"} for row in body["rows"])

    async def test_filter_by_reason_substring(self, client):
        r = await client.get("/api/v1/announces?reason=format_dedup")
        body = r.json()
        # 3 rows have a format_dedup_* reason (hold + skip + allow_grab).
        assert body["total_matched"] == 3
        for row in body["rows"]:
            assert row["decision_reason"].startswith("format_dedup_")

    async def test_filter_by_q_torrent_name(self, client):
        r = await client.get("/api/v1/announces?q=Duchy")
        body = r.json()
        assert body["total_matched"] == 2
        assert all("Duchy" in row["torrent_name"] for row in body["rows"])

    async def test_filter_by_q_author(self, client):
        r = await client.get("/api/v1/announces?q=Keleros")
        body = r.json()
        assert body["total_matched"] == 4

    async def test_combined_filters(self, client):
        """Decision + reason + q together should AND."""
        r = await client.get(
            "/api/v1/announces?decision=skip&q=Duchy&reason=format_dedup",
        )
        body = r.json()
        assert body["total_matched"] == 1
        row = body["rows"][0]
        assert row["torrent_name"] == "The Duchy"
        assert row["decision"] == "skip"
        assert row["decision_reason"] == "format_dedup_higher_priority_inflight"

    async def test_limit_caps_rows(self, client):
        r = await client.get("/api/v1/announces?limit=2")
        body = r.json()
        assert len(body["rows"]) == 2
        # total_matched is the unfiltered total — limit is just the
        # rendered subset.
        assert body["total_matched"] == 5

    async def test_empty_table_returns_empty(self, temp_db):
        """Fresh DB with no seeding → empty rows + empty counts dict."""
        app = _make_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test",
        ) as ac:
            r = await ac.get("/api/v1/announces")
            body = r.json()
            assert body["rows"] == []
            assert body["total_matched"] == 0
            assert body["decision_counts"] == {}

    async def test_filetype_surfaced_on_rows(self, client):
        """The filetype column we added in v2.9.0 P1 is what makes the
        decision_reason audit-able. Make sure it's in the response."""
        r = await client.get("/api/v1/announces?decision=hold")
        body = r.json()
        assert body["rows"][0]["filetype"] == "azw3"
