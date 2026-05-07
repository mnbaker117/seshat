"""
v2.3 Series Manager backend API tests.

Covers the mutation endpoints (`/series/promote`, `/{sid}/demote`,
PATCH, DELETE, `/{sid}/books`, DELETE `/{sid}/books/{book_id}`)
plus the new `shared` filter on the list endpoint.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
async def discovery_db(tmp_path, monkeypatch):
    from app import config as app_config
    from app.discovery import database as disco_db

    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(disco_db, "DATA_DIR", tmp_path)
    disco_db.set_active_library("test")
    await disco_db.init_db("test")
    yield tmp_path
    disco_db.set_active_library(None)


@pytest.fixture
async def client(discovery_db):
    from app.discovery.routers import series as series_router

    app = FastAPI()
    app.include_router(series_router.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _seed():
    """Seed two authors with one per-author 'Halo' series each, books
    on each. Returns (cressman_id, savarovsky_id, cressman_series_id,
    savarovsky_series_id)."""
    from app.discovery.database import get_db
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO authors (id, name, sort_name) VALUES "
            "(101, 'Eric Nylund', 'Nylund'), "
            "(102, 'Tobias S. Buckell', 'Buckell')"
        )
        await db.execute(
            "INSERT INTO series (id, name, author_id) VALUES "
            "(900, 'Halo', 101), (901, 'Halo', 102)"
        )
        await db.execute(
            "INSERT INTO books (id, title, author_id, series_id, series_index) "
            "VALUES (1, 'Reach', 101, 900, 1.0), "
            "(2, 'Cole Protocol', 102, 901, 6.0)"
        )
        await db.commit()
        return 101, 102, 900, 901
    finally:
        await db.close()


async def _series_count():
    from app.discovery.database import get_db
    db = await get_db()
    try:
        row = await (await db.execute("SELECT COUNT(*) FROM series")).fetchone()
        return row[0]
    finally:
        await db.close()


async def _series_row(sid: int):
    from app.discovery.database import get_db
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, name, author_id FROM series WHERE id = ?", (sid,)
        )).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def _book_series(book_id: int):
    from app.discovery.database import get_db
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT series_id, series_index FROM books WHERE id = ?",
            (book_id,),
        )).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


class TestPromote:
    async def test_promotes_two_per_author_into_shared(self, client):
        await _seed()

        r = await client.post(
            "/api/discovery/series/promote",
            json={"series_ids": [900, 901]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["books_moved"] == 2
        assert sorted(body["promoted_from"]) == [900, 901]
        shared_id = body["shared_id"]

        # Shared row exists with author_id NULL.
        row = await _series_row(shared_id)
        assert row["author_id"] is None
        assert row["name"] == "Halo"

        # Old rows are gone.
        assert await _series_row(900) is None
        assert await _series_row(901) is None

        # Books point at shared row.
        b1 = await _book_series(1)
        b2 = await _book_series(2)
        assert b1["series_id"] == shared_id
        assert b2["series_id"] == shared_id

    async def test_rejects_single_id(self, client):
        await _seed()
        r = await client.post(
            "/api/discovery/series/promote", json={"series_ids": [900]},
        )
        assert r.status_code == 400

    async def test_rejects_already_shared(self, client):
        from app.discovery.database import get_db
        await _seed()
        # Make 900 already shared.
        db = await get_db()
        try:
            await db.execute(
                "UPDATE series SET author_id = NULL WHERE id = 900"
            )
            await db.commit()
        finally:
            await db.close()

        r = await client.post(
            "/api/discovery/series/promote",
            json={"series_ids": [900, 901]},
        )
        assert r.status_code == 400
        assert "already-shared" in r.text

    async def test_404_on_missing_id(self, client):
        await _seed()
        r = await client.post(
            "/api/discovery/series/promote",
            json={"series_ids": [900, 999]},
        )
        assert r.status_code == 404


class TestDemote:
    async def test_splits_shared_into_per_author(self, client):
        from app.discovery.database import get_db
        # Seed a shared row with books from two authors.
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO authors (id, name, sort_name) VALUES "
                "(101, 'Eric Nylund', 'Nylund'), "
                "(102, 'Tobias S. Buckell', 'Buckell')"
            )
            await db.execute(
                "INSERT INTO series (id, name, author_id) VALUES "
                "(900, 'Halo', NULL)"
            )
            await db.execute(
                "INSERT INTO books (id, title, author_id, series_id) "
                "VALUES (1, 'Reach', 101, 900), "
                "(2, 'Cole Protocol', 102, 900)"
            )
            await db.commit()
        finally:
            await db.close()

        r = await client.post("/api/discovery/series/900/demote")
        assert r.status_code == 200
        body = r.json()
        assert body["books_moved"] == 2
        assert len(body["new_series_ids"]) == 2

        # Shared row is gone.
        assert await _series_row(900) is None

        # Each book points at its author's per-author row.
        b1 = await _book_series(1)
        b2 = await _book_series(2)
        new_ids = set(body["new_series_ids"])
        assert b1["series_id"] in new_ids
        assert b2["series_id"] in new_ids
        assert b1["series_id"] != b2["series_id"]

    async def test_rejects_per_author_row(self, client):
        await _seed()
        r = await client.post("/api/discovery/series/900/demote")
        assert r.status_code == 400


class TestRename:
    async def test_renames_series(self, client):
        await _seed()
        r = await client.patch(
            "/api/discovery/series/900", json={"name": "Halo Saga"},
        )
        assert r.status_code == 200
        row = await _series_row(900)
        assert row["name"] == "Halo Saga"

    async def test_rejects_empty_name(self, client):
        await _seed()
        r = await client.patch(
            "/api/discovery/series/900", json={"name": "  "},
        )
        assert r.status_code == 400

    async def test_409_on_conflict(self, client):
        # Seed a per-author row (900) and another for the same author
        # with a different name (902). Renaming 900 to "Other" collides
        # with 902.
        from app.discovery.database import get_db
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO authors (id, name, sort_name) VALUES "
                "(101, 'A', 'A')"
            )
            await db.execute(
                "INSERT INTO series (id, name, author_id) VALUES "
                "(900, 'Halo', 101), (902, 'Other', 101)"
            )
            await db.commit()
        finally:
            await db.close()

        r = await client.patch(
            "/api/discovery/series/900", json={"name": "Other"},
        )
        assert r.status_code == 409
        # FastAPI wraps the dict body in {"detail": ...}.
        assert r.json()["detail"]["conflict_id"] == 902


class TestDelete:
    async def test_deletes_series_and_orphans_books(self, client):
        await _seed()
        r = await client.delete("/api/discovery/series/900")
        assert r.status_code == 200
        body = r.json()
        assert body["books_orphaned"] == 1

        # Series gone, book is standalone.
        assert await _series_row(900) is None
        b = await _book_series(1)
        assert b["series_id"] is None
        assert b["series_index"] is None


class TestMembership:
    async def test_add_books_to_series(self, client):
        from app.discovery.database import get_db
        await _seed()
        # Insert a standalone book to add into a series.
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO books (id, title, author_id) "
                "VALUES (3, 'Standalone', 101)"
            )
            await db.commit()
        finally:
            await db.close()

        r = await client.post(
            "/api/discovery/series/900/books",
            json={"book_ids": [3], "indices": {"3": 2.0}},
        )
        assert r.status_code == 200
        b = await _book_series(3)
        assert b["series_id"] == 900
        assert b["series_index"] == 2.0

    async def test_remove_book_from_series(self, client):
        await _seed()
        r = await client.delete("/api/discovery/series/900/books/1")
        assert r.status_code == 200
        b = await _book_series(1)
        assert b["series_id"] is None
        assert b["series_index"] is None

    async def test_remove_404_if_not_member(self, client):
        await _seed()
        # book 2 is on series 901, not 900.
        r = await client.delete("/api/discovery/series/900/books/2")
        assert r.status_code == 404


class TestSharedFilter:
    async def test_shared_true_returns_shared_only(self, client):
        from app.discovery.database import get_db
        await _seed()
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO series (id, name, author_id) "
                "VALUES (902, 'Halo Universe', NULL)"
            )
            await db.commit()
        finally:
            await db.close()

        r = await client.get("/api/discovery/series?shared=true")
        ids = {s["id"] for s in r.json()["series"]}
        assert ids == {902}

    async def test_shared_false_returns_per_author_only(self, client):
        from app.discovery.database import get_db
        await _seed()
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO series (id, name, author_id) "
                "VALUES (902, 'Halo Universe', NULL)"
            )
            await db.commit()
        finally:
            await db.close()

        r = await client.get("/api/discovery/series?shared=false")
        ids = {s["id"] for s in r.json()["series"]}
        assert ids == {900, 901}
