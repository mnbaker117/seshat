"""
v2.3.5 push-back endpoints.

Two layers of coverage:

  - **Dispatch tests** stub out the three push-back helpers
    (`push_abs`, `push_calibre_full`, `push_cwa`) so we exercise
    `POST /books/{bid}/push` routing + user_edited_fields clearing
    + bulk verb behavior without standing up real ABS/calibredb/CWA.

  - **Translation tests** exercise the pure helpers
    (`_build_abs_metadata`, `_format_calibredb_value`,
    `_format_cwa_value`) directly — they're side-effect free.

The actual HTTP/subprocess dispatch is covered at integration time
(Mark's UAT against the live container).
"""
from __future__ import annotations

import json

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
    from app.discovery.routers import metadata as metadata_router

    app = FastAPI()
    app.include_router(metadata_router.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ── seed helpers ─────────────────────────────────────────────────────


async def _seed_book(
    book_id: int = 1,
    title: str = "My Book",
    description: str | None = None,
    audiobookshelf_id: str | None = None,
    calibre_id: int | None = None,
    user_edited: list[str] | None = None,
    series_id: int | None = None,
    series_name: str | None = None,
):
    from app.discovery.database import get_db
    from app.metadata.author_names import normalize_author_name
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO authors (id, name, sort_name, "
            "normalized_name) VALUES (101, 'A', 'A', ?)",
            (normalize_author_name("A"),),
        )
        if series_name and series_id:
            await db.execute(
                "INSERT OR IGNORE INTO series (id, name, author_id) "
                "VALUES (?, ?, 101)",
                (series_id, series_name),
            )
        await db.execute(
            "INSERT INTO books (id, title, author_id, description, "
            "audiobookshelf_id, calibre_id, series_id, source, owned, "
            "user_edited_fields) "
            "VALUES (?, ?, 101, ?, ?, ?, ?, ?, ?, ?)",
            (
                book_id, title, description, audiobookshelf_id, calibre_id,
                series_id,
                "audiobookshelf" if audiobookshelf_id else
                "calibre" if calibre_id else "goodreads",
                1 if (audiobookshelf_id or calibre_id) else 0,
                json.dumps(user_edited or []),
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def _book_row(book_id: int) -> dict:
    from app.discovery.database import get_db
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT * FROM books WHERE id = ?", (book_id,),
        )).fetchone()
        return dict(row) if row else {}
    finally:
        await db.close()


def _stub_helpers(monkeypatch, *, abs_calls=None, cal_calls=None,
                  cwa_calls=None,
                  cal_unavailable: bool = False,
                  cwa_unavailable: bool = False,
                  abs_fails: bool = False):
    """Replace the three push-back helpers with deterministic stubs.

    Each call list (`abs_calls`/`cal_calls`/`cwa_calls`) gets appended
    `(book_id, fields)` so tests can assert dispatch routed correctly.
    """
    from app.discovery import push_back

    async def _abs(db, book, fields):
        if abs_calls is not None:
            abs_calls.append((book["id"], list(fields)))
        if abs_fails:
            raise push_back.PushFailed("ABS rejected the push")
        return {"applied": list(fields), "failed": []}

    async def _cal(db, book, fields):
        if cal_calls is not None:
            cal_calls.append((book["id"], list(fields)))
        if cal_unavailable:
            raise push_back.PushUnavailable(
                "calibredb not found in this image",
            )
        return {"applied": list(fields), "failed": []}

    async def _cwa(db, book, fields):
        if cwa_calls is not None:
            cwa_calls.append((book["id"], list(fields)))
        if cwa_unavailable:
            raise push_back.PushUnavailable("CWA push not configured")
        return {"applied": list(fields), "failed": []}

    monkeypatch.setattr(push_back, "push_abs", _abs)
    monkeypatch.setattr(push_back, "push_calibre_full", _cal)
    monkeypatch.setattr(push_back, "push_cwa", _cwa)


# ── Dispatch + clearing tests ────────────────────────────────────────


class TestPushDispatch:
    async def test_abs_push_routes_to_abs_helper(self, client, monkeypatch):
        abs_calls: list = []
        _stub_helpers(monkeypatch, abs_calls=abs_calls)
        await _seed_book(audiobookshelf_id="abs-1",
                         user_edited=["description"])
        r = await client.post(
            "/api/discovery/books/1/push",
            json={"source": "abs", "fields": ["description"]},
        )
        assert r.status_code == 200
        assert abs_calls == [(1, ["description"])]

    async def test_calibre_push_prefers_calibredb(self, client, monkeypatch):
        cal_calls: list = []
        cwa_calls: list = []
        _stub_helpers(monkeypatch, cal_calls=cal_calls, cwa_calls=cwa_calls)
        await _seed_book(calibre_id=42, user_edited=["title"])
        r = await client.post(
            "/api/discovery/books/1/push",
            json={"source": "calibre", "fields": ["title"]},
        )
        assert r.status_code == 200
        assert cal_calls == [(1, ["title"])]
        assert cwa_calls == []  # CWA never reached

    async def test_calibre_push_falls_back_to_cwa(self, client, monkeypatch):
        cal_calls: list = []
        cwa_calls: list = []
        _stub_helpers(
            monkeypatch, cal_calls=cal_calls, cwa_calls=cwa_calls,
            cal_unavailable=True,
        )
        await _seed_book(calibre_id=42, user_edited=["title"])
        r = await client.post(
            "/api/discovery/books/1/push",
            json={"source": "calibre", "fields": ["title"]},
        )
        assert r.status_code == 200
        assert cal_calls == [(1, ["title"])]
        assert cwa_calls == [(1, ["title"])]

    async def test_calibre_push_409_when_neither_configured(
        self, client, monkeypatch,
    ):
        _stub_helpers(
            monkeypatch, cal_unavailable=True, cwa_unavailable=True,
        )
        await _seed_book(calibre_id=42, user_edited=["title"])
        r = await client.post(
            "/api/discovery/books/1/push",
            json={"source": "calibre", "fields": ["title"]},
        )
        assert r.status_code == 409
        assert "CWA" in r.json()["detail"]

    async def test_push_502_on_upstream_failure(self, client, monkeypatch):
        _stub_helpers(monkeypatch, abs_fails=True)
        await _seed_book(audiobookshelf_id="abs-1")
        r = await client.post(
            "/api/discovery/books/1/push",
            json={"source": "abs", "fields": ["description"]},
        )
        assert r.status_code == 502

    async def test_push_404_on_unknown_book(self, client, monkeypatch):
        _stub_helpers(monkeypatch)
        r = await client.post(
            "/api/discovery/books/99/push",
            json={"source": "abs", "fields": ["description"]},
        )
        assert r.status_code == 404

    async def test_push_400_on_invalid_source(self, client, monkeypatch):
        _stub_helpers(monkeypatch)
        await _seed_book(audiobookshelf_id="abs-1")
        r = await client.post(
            "/api/discovery/books/1/push",
            json={"source": "wrong", "fields": ["description"]},
        )
        assert r.status_code == 400

    async def test_push_400_on_missing_fields(self, client, monkeypatch):
        _stub_helpers(monkeypatch)
        await _seed_book(audiobookshelf_id="abs-1")
        r = await client.post(
            "/api/discovery/books/1/push",
            json={"source": "abs"},
        )
        assert r.status_code == 400


class TestPushClearsUserEdited:
    async def test_successful_push_clears_field(self, client, monkeypatch):
        _stub_helpers(monkeypatch)
        await _seed_book(
            audiobookshelf_id="abs-1",
            user_edited=["description", "title"],
        )
        await client.post(
            "/api/discovery/books/1/push",
            json={"source": "abs", "fields": ["description"]},
        )
        uef = json.loads((await _book_row(1))["user_edited_fields"])
        assert "description" not in uef
        assert "title" in uef                  # untouched

    async def test_failed_push_leaves_uef_intact(self, client, monkeypatch):
        _stub_helpers(monkeypatch, abs_fails=True)
        await _seed_book(
            audiobookshelf_id="abs-1",
            user_edited=["description"],
        )
        await client.post(
            "/api/discovery/books/1/push",
            json={"source": "abs", "fields": ["description"]},
        )
        uef = json.loads((await _book_row(1))["user_edited_fields"])
        assert "description" in uef


class TestPushBulk:
    async def test_all_user_edited_iterates_intersection(
        self, client, monkeypatch,
    ):
        # `narrator` is ABS-only, `description` is shared, `cover_path`
        # is shared. The bulk filter pushes only fields ABS can write.
        abs_calls: list = []
        _stub_helpers(monkeypatch, abs_calls=abs_calls)
        await _seed_book(
            audiobookshelf_id="abs-1",
            user_edited=["description", "narrator", "cover_path"],
        )
        r = await client.post(
            "/api/discovery/books/1/push",
            json={"source": "abs", "all_user_edited": True},
        )
        assert r.status_code == 200
        # All three are in the abs col_map. The dispatcher passes them
        # through; the helper would filter unsupported ones internally.
        called_with = abs_calls[0][1]
        assert "description" in called_with
        assert "narrator" in called_with
        # cover_path is in the abs col_map too (per COMPARE_FIELDS),
        # though we deferred its push. The dispatcher doesn't know
        # that; that's the helper's responsibility.
        assert "cover_path" in called_with

    async def test_bulk_with_empty_uef_is_noop(self, client, monkeypatch):
        abs_calls: list = []
        _stub_helpers(monkeypatch, abs_calls=abs_calls)
        await _seed_book(audiobookshelf_id="abs-1", user_edited=[])
        r = await client.post(
            "/api/discovery/books/1/push",
            json={"source": "abs", "all_user_edited": True},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] == []
        # Helper never invoked — nothing to push.
        assert abs_calls == []


# ── Translation helper unit tests ───────────────────────────────────


class TestBuildAbsMetadata:
    def test_simple_scalar_fields(self):
        from app.discovery.push_back import _build_abs_metadata
        book = {
            "title": "T", "description": "D",
            "asin": "B0X", "isbn": "9780",
            "language": "eng", "publisher": "Pub",
            "abridged": 1,
            "narrator": "Alice, Bob",
            "pub_date": "2024-05-01",
        }
        md = _build_abs_metadata(
            book,
            ["title", "description", "asin", "isbn", "language",
             "publisher", "abridged", "narrator", "pub_date"],
        )
        assert md["title"] == "T"
        assert md["description"] == "D"
        assert md["narrators"] == ["Alice", "Bob"]
        assert md["abridged"] is True
        assert md["publishedDate"] == "2024-05-01"

    def test_series_collapses(self):
        from app.discovery.push_back import _build_abs_metadata
        md = _build_abs_metadata(
            {"series_name": "Halo", "series_index": 3.0},
            ["series_name", "series_index"],
        )
        assert md["series"] == [{"name": "Halo", "sequence": "3"}]

    def test_series_index_fractional_kept_as_string(self):
        from app.discovery.push_back import _build_abs_metadata
        md = _build_abs_metadata(
            {"series_name": "Foo", "series_index": 1.5},
            ["series_name", "series_index"],
        )
        assert md["series"][0]["sequence"] == "1.5"

    def test_empty_series_clears(self):
        from app.discovery.push_back import _build_abs_metadata
        md = _build_abs_metadata(
            {"series_name": None, "series_index": None},
            ["series_name"],
        )
        assert md["series"] == []

    def test_unknown_field_dropped(self):
        from app.discovery.push_back import _build_abs_metadata
        md = _build_abs_metadata(
            {"title": "T", "tags": "x,y"},
            ["title", "tags"],
        )
        assert "title" in md
        assert "tags" not in md          # tags not in ABS map (yet)


class TestFormatCalibredbValue:
    def test_scalar_string(self):
        from app.discovery.push_back import _format_calibredb_value
        assert _format_calibredb_value("title", "  Hello  ") == "Hello"

    def test_blank_string_returns_none(self):
        from app.discovery.push_back import _format_calibredb_value
        assert _format_calibredb_value("title", "") is None
        assert _format_calibredb_value("title", "   ") is None

    def test_rating_rounds_to_int(self):
        from app.discovery.push_back import _format_calibredb_value
        # books.rating is REAL (e.g. 8.5). Calibre stores int 0-10.
        assert _format_calibredb_value("rating", 8.5) == "8"
        assert _format_calibredb_value("rating", 10) == "10"

    def test_series_index_integral_drops_decimal(self):
        from app.discovery.push_back import _format_calibredb_value
        assert _format_calibredb_value("series_index", 3.0) == "3"
        assert _format_calibredb_value("series_index", 3.5) == "3.5"

    def test_none_returns_none(self):
        from app.discovery.push_back import _format_calibredb_value
        assert _format_calibredb_value("title", None) is None


class TestFormatCwaValue:
    def test_scalar_string_strips(self):
        from app.discovery.push_back import _format_cwa_value
        assert _format_cwa_value("book_title", "  X  ") == "X"

    def test_empty_returns_none(self):
        from app.discovery.push_back import _format_cwa_value
        assert _format_cwa_value("book_title", "") is None

    def test_rating_int_string(self):
        from app.discovery.push_back import _format_cwa_value
        assert _format_cwa_value("rating", 9.5) == "10"
