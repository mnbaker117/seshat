"""
HTTP-level tests for the author manager router.

Walks through:
  - empty overview
  - bulk add to allowed
  - paginated list
  - search filter
  - move allowed → ignored
  - delete from a list
  - rejection of writes to tentative_review
"""
import httpx
import pytest
from fastapi import FastAPI

from app.database import get_db
from app.routers.authors import router as authors_router
from app.storage import authors as authors_storage


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(authors_router)
    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.fixture
async def app(temp_db):
    return _make_app()


class TestOverview:
    async def test_empty(self, app):
        async with await _client(app) as c:
            r = await c.get("/api/v1/authors")
            assert r.status_code == 200
            body = r.json()
            assert body["counts"]["allowed"] == 0
            assert body["counts"]["ignored"] == 0
            assert body["counts"]["tentative_review"] == 0


class TestAdd:
    async def test_bulk_add_to_allowed(self, app):
        async with await _client(app) as c:
            r = await c.post(
                "/api/v1/authors/allowed",
                json={"names": ["Brandon Sanderson", "Isaac Asimov"]},
            )
            assert r.status_code == 200
            assert r.json() == {"added": 2, "skipped": 0}

            r2 = await c.get("/api/v1/authors/allowed")
            body = r2.json()
            assert body["count"] == 2
            assert {row["normalized"] for row in body["items"]} == {
                "brandon sanderson",
                "isaac asimov",
            }

    async def test_skips_blanks_and_duplicates(self, app):
        async with await _client(app) as c:
            await c.post(
                "/api/v1/authors/allowed",
                json={"names": ["Already Here"]},
            )
            r = await c.post(
                "/api/v1/authors/allowed",
                json={"names": ["", "Already Here", "  ", "New One"]},
            )
            body = r.json()
            assert body["added"] == 1
            assert body["skipped"] == 3

    async def test_tentative_review_rejects_manual_add(self, app):
        async with await _client(app) as c:
            r = await c.post(
                "/api/v1/authors/tentative_review",
                json={"names": ["Anyone"]},
            )
            assert r.status_code == 400


class TestListSearch:
    async def test_search_filters_by_normalized_substring(self, app):
        async with await _client(app) as c:
            await c.post(
                "/api/v1/authors/allowed",
                json={
                    "names": [
                        "Brandon Sanderson",
                        "Brandon Mull",
                        "Isaac Asimov",
                    ]
                },
            )
            r = await c.get("/api/v1/authors/allowed?search=brandon")
            body = r.json()
            assert body["count"] == 3  # total count is unfiltered
            normalized = {row["normalized"] for row in body["items"]}
            assert normalized == {"brandon sanderson", "brandon mull"}


class TestMove:
    async def test_move_allowed_to_ignored(self, app):
        async with await _client(app) as c:
            await c.post(
                "/api/v1/authors/allowed",
                json={"names": ["Author X"]},
            )
            r = await c.post(
                "/api/v1/authors/allowed/author x/move",
                json={"to": "ignored"},
            )
            assert r.status_code == 200
            assert r.json()["ok"] is True

            db = await get_db()
            try:
                assert await authors_storage.is_ignored(db, "Author X")
                assert not await authors_storage.is_allowed(db, "Author X")
            finally:
                await db.close()

    async def test_move_with_same_target_400(self, app):
        async with await _client(app) as c:
            r = await c.post(
                "/api/v1/authors/allowed/foo/move",
                json={"to": "allowed"},
            )
            assert r.status_code == 400


class TestDelete:
    async def test_delete_removes_row(self, app):
        async with await _client(app) as c:
            await c.post(
                "/api/v1/authors/allowed",
                json={"names": ["Doomed Author"]},
            )
            r = await c.delete("/api/v1/authors/allowed/doomed author")
            assert r.status_code == 200
            assert r.json()["ok"] is True

            r2 = await c.get("/api/v1/authors/allowed")
            assert r2.json()["count"] == 0

    async def test_delete_unknown_returns_ok_false(self, app):
        async with await _client(app) as c:
            r = await c.delete("/api/v1/authors/allowed/never existed")
            assert r.status_code == 200
            assert r.json()["ok"] is False
