"""
HTTP-level tests for `/api/v1/works/...`.

Exercises the full endpoint surface: list, get, manual link, unlink,
rebuild, and the per-author-preferences CRUD subtree.

Shared setup:
  * `temp_db` from the root conftest provides a fresh pipeline DB
    with the work_links + author_format_preferences tables already
    migrated in.
  * `_hydrate_links` joins against per-library discovery DBs for
    display metadata. We stub it with `monkeypatch` in the tests
    that care about link responses — the storage-level behavior is
    what the Works router actually guarantees.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.routers.works import router as works_router
from app.works import preferences, storage


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(works_router)
    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.fixture
def no_hydrate(monkeypatch):
    """Bypass discovery-DB hydration so the router returns raw link rows.

    `_hydrate_links` opens per-library discovery DBs; there are none in
    a bare `temp_db` fixture. These tests assert on the core link
    fields, not on the joined title/author cover — stubbing keeps the
    tests focused and fast.
    """
    from app.routers import works as works_module
    from app.routers.works import _link_to_out

    async def stub(links):
        return [_link_to_out(l) for l in links]

    monkeypatch.setattr(works_module, "_hydrate_links", stub)


@pytest.fixture
async def app(temp_db, no_hydrate):
    return _make_app()


# ─── List + Get ───────────────────────────────────────────────

class TestListWorks:
    async def test_empty(self, app):
        async with await _client(app) as c:
            r = await c.get("/api/v1/works")
            assert r.status_code == 200
            body = r.json()
            assert body == {"total": 0, "items": []}

    async def test_lists_distinct_work_ids(self, app):
        w1 = storage.generate_work_id()
        w2 = storage.generate_work_id()
        await storage.merge_books_into_work(
            work_id=w1,
            members=[
                {"library_slug": "cal", "book_id": 1, "content_type": "ebook"},
                {"library_slug": "abs", "book_id": 10, "content_type": "audiobook"},
            ],
        )
        await storage.create_link(
            work_id=w2, library_slug="cal", book_id=2, content_type="ebook",
        )

        async with await _client(app) as c:
            r = await c.get("/api/v1/works")
            body = r.json()

        assert body["total"] == 2
        ids = {item["work_id"] for item in body["items"]}
        assert ids == {w1, w2}
        # The 2-member work carries both links.
        multi = next(i for i in body["items"] if i["work_id"] == w1)
        assert len(multi["links"]) == 2

    async def test_filters_by_library_slug(self, app):
        w1 = storage.generate_work_id()
        w2 = storage.generate_work_id()
        await storage.create_link(
            work_id=w1, library_slug="cal", book_id=1, content_type="ebook",
        )
        await storage.create_link(
            work_id=w2, library_slug="abs", book_id=2, content_type="audiobook",
        )
        async with await _client(app) as c:
            r = await c.get("/api/v1/works?library_slug=cal")
            body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["work_id"] == w1

    async def test_pagination_offset_and_limit(self, app):
        for i in range(5):
            await storage.create_link(
                work_id=storage.generate_work_id(),
                library_slug="cal", book_id=i + 1, content_type="ebook",
            )
        async with await _client(app) as c:
            r = await c.get("/api/v1/works?limit=2&offset=2")
            body = r.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2


class TestGetWork:
    async def test_unknown_work_id_is_404(self, app):
        async with await _client(app) as c:
            r = await c.get("/api/v1/works/does-not-exist")
            assert r.status_code == 404

    async def test_returns_members_sorted(self, app):
        wid = storage.generate_work_id()
        await storage.merge_books_into_work(
            work_id=wid,
            members=[
                {"library_slug": "cal", "book_id": 1, "content_type": "ebook"},
                {"library_slug": "abs", "book_id": 10, "content_type": "audiobook"},
            ],
        )
        async with await _client(app) as c:
            r = await c.get(f"/api/v1/works/{wid}")
            body = r.json()

        assert body["work_id"] == wid
        assert len(body["links"]) == 2
        slugs = {l["library_slug"] for l in body["links"]}
        assert slugs == {"cal", "abs"}


# ─── Manual link ──────────────────────────────────────────────

class TestManualLink:
    async def test_link_mints_new_work_id_when_omitted(self, app):
        body = {"members": [
            {"library_slug": "cal", "book_id": 1, "content_type": "ebook"},
            {"library_slug": "abs", "book_id": 10, "content_type": "audiobook"},
        ]}
        async with await _client(app) as c:
            r = await c.post("/api/v1/works/link", json=body)
            assert r.status_code == 200
            out = r.json()

        assert out["work_id"]
        assert len(out["links"]) == 2
        for link in out["links"]:
            assert link["link_source"] == "manual"

    async def test_link_merges_into_existing_work_id(self, app):
        existing = storage.generate_work_id()
        await storage.create_link(
            work_id=existing, library_slug="cal",
            book_id=1, content_type="ebook",
        )
        body = {
            "work_id": existing,
            "members": [{
                "library_slug": "abs", "book_id": 10,
                "content_type": "audiobook",
            }],
        }
        async with await _client(app) as c:
            r = await c.post("/api/v1/works/link", json=body)
            out = r.json()

        assert out["work_id"] == existing
        assert len(out["links"]) == 2

    async def test_link_rehomes_existing_auto_link_to_manual(self, app):
        """An existing auto-link member re-homed via the API flips to
        manual so the auto-matcher won't stomp it next rebuild."""
        other_work = storage.generate_work_id()
        await storage.create_link(
            work_id=other_work, library_slug="cal",
            book_id=1, content_type="ebook", link_source="auto",
        )
        target_work = storage.generate_work_id()
        # Plant the target work so `work_id` validation passes.
        await storage.create_link(
            work_id=target_work, library_slug="abs",
            book_id=99, content_type="audiobook",
        )

        body = {
            "work_id": target_work,
            "members": [{
                "library_slug": "cal", "book_id": 1, "content_type": "ebook",
            }],
        }
        async with await _client(app) as c:
            r = await c.post("/api/v1/works/link", json=body)
            assert r.status_code == 200

        link = await storage.get_link("cal", 1)
        assert link.work_id == target_work
        assert link.link_source == "manual"

    async def test_link_to_nonexistent_work_id_is_404(self, app):
        body = {
            "work_id": "no-such-work",
            "members": [{
                "library_slug": "cal", "book_id": 1, "content_type": "ebook",
            }],
        }
        async with await _client(app) as c:
            r = await c.post("/api/v1/works/link", json=body)
            assert r.status_code == 404

    async def test_empty_members_list_is_400(self, app):
        async with await _client(app) as c:
            r = await c.post("/api/v1/works/link", json={"members": []})
            assert r.status_code == 400

    async def test_member_missing_field_is_400(self, app):
        body = {"members": [{
            "library_slug": "cal", "book_id": 1,  # no content_type
        }]}
        async with await _client(app) as c:
            r = await c.post("/api/v1/works/link", json=body)
            assert r.status_code == 400


# ─── Unlink ───────────────────────────────────────────────────

class TestUnlink:
    async def test_unlink_existing_returns_ok_true(self, app):
        wid = storage.generate_work_id()
        await storage.create_link(
            work_id=wid, library_slug="cal",
            book_id=1, content_type="ebook",
        )
        async with await _client(app) as c:
            r = await c.delete("/api/v1/works/link/cal/1")
            assert r.status_code == 200
            assert r.json() == {"ok": True}
        assert await storage.get_link("cal", 1) is None

    async def test_unlink_unknown_returns_ok_false(self, app):
        async with await _client(app) as c:
            r = await c.delete("/api/v1/works/link/cal/9999")
            assert r.json() == {"ok": False}


# ─── Rebuild ──────────────────────────────────────────────────

class TestRebuild:
    async def test_rebuild_with_no_libraries_returns_zero_counts(
        self, app, monkeypatch,
    ):
        """With <2 discovered libraries there's nothing to cross-link;
        rebuild should run its reconcile pass and return safely."""
        monkeypatch.setattr("app.works.matcher.state._discovered_libraries", [])
        async with await _client(app) as c:
            r = await c.post("/api/v1/works/rebuild")
            assert r.status_code == 200
            body = r.json()
        assert body["works_created"] == 0
        assert body["links_added"] == 0


# ─── Author preferences ──────────────────────────────────────

class TestAuthorPreferences:
    async def test_get_unset_is_404(self, app):
        async with await _client(app) as c:
            r = await c.get("/api/v1/works/author-preferences/Nobody")
            assert r.status_code == 404

    async def test_put_creates_and_get_returns(self, app):
        async with await _client(app) as c:
            r = await c.put(
                "/api/v1/works/author-preferences/Brandon Sanderson",
                json={"tracking_mode": "audiobook"},
            )
            assert r.status_code == 200
            assert r.json()["tracking_mode"] == "audiobook"

            r2 = await c.get(
                "/api/v1/works/author-preferences/Brandon Sanderson",
            )
            assert r2.json()["tracking_mode"] == "audiobook"

    async def test_put_invalid_mode_is_400(self, app):
        async with await _client(app) as c:
            r = await c.put(
                "/api/v1/works/author-preferences/Alice",
                json={"tracking_mode": "ebook-only"},
            )
            assert r.status_code == 400

    async def test_list_returns_all(self, app):
        await preferences.set_preference("Alice", "ebook")
        await preferences.set_preference("Bob", "audiobook")
        async with await _client(app) as c:
            r = await c.get("/api/v1/works/author-preferences")
            body = r.json()
        norms = {p["normalized_name"] for p in body}
        assert norms == {"alice", "bob"}

    async def test_delete_returns_ok_true_then_false(self, app):
        await preferences.set_preference("Alice", "ebook")
        async with await _client(app) as c:
            r = await c.delete("/api/v1/works/author-preferences/Alice")
            assert r.json() == {"ok": True}
            # Second delete — row is gone.
            r2 = await c.delete("/api/v1/works/author-preferences/Alice")
            assert r2.json() == {"ok": False}
