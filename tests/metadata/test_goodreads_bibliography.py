"""
Tests for `app.metadata.goodreads_bibliography` — the T5
`/author/list/{id}` walker that resolves a goodreads_book_id by
fuzzy-matching titles in an author's bibliography.

Scope:
  - Page parser extracts (book_id, work_id, title, pub_year, rating)
  - Single-page hit returns the book_id with no further pagination
  - Multi-page walk hits on later pages and persists cumulative cache
  - Cache hit serves from memory without HTTP
  - Exhaustion (full walk, no match) marks fully_indexed and returns None
  - Subsequent lookup against fully_indexed cache is HTTP-free
  - Soft-block on a page returns the `_soft_blocked` marker cleanly
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Callable

import pytest


@pytest.fixture
def isolated_id_cache(monkeypatch, tmp_path):
    """Tmp DATA_DIR + reset goodreads_session singleton + reset id_cache.

    The bibliography module persists per-author caches via
    `id_cache.put_author_bib`, and routes HTTP through the
    `goodreads_session` singleton. Both must be sandboxed so a test
    can't bleed state into the real DATA_DIR or another test.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    importlib.reload(app.config)
    import app.metadata.goodreads_session as gs
    importlib.reload(gs)
    gs.reset_session_for_tests()
    import app.metadata.id_cache as ic
    importlib.reload(ic)
    monkeypatch.setattr(ic, "_db_path", lambda: tmp_path / "id_cache.db")
    import app.metadata.goodreads_bibliography as gb
    importlib.reload(gb)
    return gb


def _make_resp(status: int, body: bytes = b"") -> SimpleNamespace:
    text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else body
    return SimpleNamespace(status_code=status, content=body, text=text)


def _row(book_id: str, title: str, *, work_id: str = "", year: int | None = None) -> str:
    """Render a minimal Goodreads schema.org/Book table row."""
    work_link = (
        f'<a class="greyText" href="/work/editions/{work_id}-foo">5 editions</a>'
        if work_id else ""
    )
    pub = f"published {year} —" if year else ""
    return f'''
<tr itemscope itemtype="http://schema.org/Book">
  <td><a href="/book/show/{book_id}.{title.replace(' ','_')}">cover</a></td>
  <td>
    <a class="bookTitle" href="/book/show/{book_id}.{title.replace(' ','_')}" itemprop="url">
      <span itemprop="name">{title}</span>
    </a>
    <div>
      <span class="greyText smallText uitext">
        <span class="minirating">4.50 avg rating — 100 ratings</span>
        — {pub}
        {work_link}
      </span>
    </div>
  </td>
</tr>
'''


def _page_html(rows: list[str], *, has_next: bool) -> str:
    """Wrap rows in just enough page chrome for the parser + has_more detector."""
    pagination = (
        '<div class="pagination"><a href="/author/list/X?page=2">2</a></div>'
        if has_next else ""
    )
    return f'''<html><body><table>{"".join(rows)}</table>{pagination}</body></html>'''


@pytest.fixture
def stub_session(monkeypatch):
    """Build a fake `GoodreadsSession.get()` hooked to a callable.

    Returns a setter `set_handler(fn)` so each test can plug in a
    request handler that maps URL → SimpleNamespace response.
    """
    from app.metadata import goodreads_session

    state = {"handler": None, "calls": []}

    class FakeSession:
        async def get(self, url, **kwargs):
            state["calls"].append(url)
            handler = state["handler"]
            if handler is None:
                return _make_resp(404)
            resp = handler(url)
            return resp

    fake = FakeSession()

    async def fake_get_session(rate_limit=None):
        return fake

    monkeypatch.setattr(goodreads_session, "get_session", fake_get_session)
    return state


class TestBibliographyPageParser:
    def test_parser_extracts_rows_and_has_more(self, isolated_id_cache):
        gb = isolated_id_cache
        html = _page_html(
            [
                _row("68428", "Mistborn The Final Empire", work_id="66322", year=2006),
                _row("7235533", "The Way of Kings", work_id="8134945", year=2010),
            ],
            has_next=True,
        )
        entries, has_more, soft = gb._parse_page(html)
        assert len(entries) == 2
        assert entries[0].book_id == "68428"
        assert entries[0].work_id == "66322"
        assert entries[0].pub_year == 2006
        assert entries[0].avg_rating == 4.50
        assert entries[0].ratings_count == 100
        assert has_more is True
        assert soft is False

    def test_parser_detects_last_page(self, isolated_id_cache):
        gb = isolated_id_cache
        html = _page_html([_row("999", "Last Book")], has_next=False)
        _, has_more, _ = gb._parse_page(html)
        assert has_more is False


class TestBibliographyWalk:
    @pytest.mark.asyncio
    async def test_first_page_hit_returns_immediately(
        self, isolated_id_cache, stub_session,
    ):
        gb = isolated_id_cache
        page1 = _page_html(
            [
                _row("68428", "Mistborn The Final Empire"),
                _row("7235533", "The Way of Kings"),
            ],
            has_next=True,
        )
        stub_session["handler"] = lambda url: _make_resp(200, page1.encode())

        result = await gb.find_book_in_bibliography(
            "38550", "Mistborn The Final Empire",
        )
        assert result == "68428"
        # Only one HTTP call — early-stop on page 1 hit.
        assert len(stub_session["calls"]) == 1

    @pytest.mark.asyncio
    async def test_walk_continues_to_page_two_on_miss(
        self, isolated_id_cache, stub_session,
    ):
        gb = isolated_id_cache
        page1 = _page_html(
            [_row("11111", "Some Other Book")],
            has_next=True,
        )
        page2 = _page_html(
            [_row("68428", "Mistborn The Final Empire")],
            has_next=False,
        )

        def handler(url):
            if "page=2" in url:
                return _make_resp(200, page2.encode())
            return _make_resp(200, page1.encode())

        stub_session["handler"] = handler
        result = await gb.find_book_in_bibliography(
            "38550", "Mistborn The Final Empire",
        )
        assert result == "68428"
        assert len(stub_session["calls"]) == 2

    @pytest.mark.asyncio
    async def test_exhaustion_returns_none_and_marks_fully_indexed(
        self, isolated_id_cache, stub_session,
    ):
        gb = isolated_id_cache
        # One page, no match for the looked-up title.
        page1 = _page_html([_row("11111", "Some Other Book")], has_next=False)
        stub_session["handler"] = lambda url: _make_resp(200, page1.encode())

        result = await gb.find_book_in_bibliography(
            "38550", "Title That Does Not Exist",
        )
        assert result is None

        # Cache should now be fully_indexed.
        from app.metadata import id_cache
        cached = id_cache.get_author_bib("38550") or []
        meta = next((e for e in cached if e.get("_meta")), None)
        assert meta is not None
        assert meta["fully_indexed"] is True

    @pytest.mark.asyncio
    async def test_fully_indexed_cache_skips_http(
        self, isolated_id_cache, stub_session,
    ):
        gb = isolated_id_cache
        # Pre-seed the cache as fully indexed with no matching title.
        from app.metadata import id_cache
        id_cache.put_author_bib("38550", [
            {"_meta": True, "pages_walked": 1, "fully_indexed": True},
            {"book_id": "11111", "title": "Some Other Book"},
        ])
        stub_session["handler"] = lambda url: pytest.fail(
            "fully_indexed cache should never hit HTTP"
        )

        result = await gb.find_book_in_bibliography(
            "38550", "Title Not In Cache",
        )
        assert result is None
        assert len(stub_session["calls"]) == 0

    @pytest.mark.asyncio
    async def test_soft_block_returns_marker_without_caching(
        self, isolated_id_cache, stub_session,
    ):
        gb = isolated_id_cache
        # Cloudflare-style 202 with empty body.
        stub_session["handler"] = lambda url: _make_resp(202, b"")

        result = await gb.find_book_in_bibliography(
            "38550", "Mistborn",
        )
        assert result == "_soft_blocked"

        # Soft-block must NOT poison the cache (no fully_indexed flip).
        from app.metadata import id_cache
        cached = id_cache.get_author_bib("38550")
        # Either the cache is unset or its meta is partial — but never
        # marked fully_indexed when the walk was abandoned.
        if cached:
            meta = next((e for e in cached if e.get("_meta")), None)
            assert not (meta and meta.get("fully_indexed"))

    @pytest.mark.asyncio
    async def test_403_during_walk_treated_as_soft_block(
        self, isolated_id_cache, stub_session,
    ):
        # v2.13.2 detector expansion: CloudFront 403 also flips soft-block.
        gb = isolated_id_cache
        stub_session["handler"] = lambda url: _make_resp(403, b"forbidden")

        result = await gb.find_book_in_bibliography("38550", "Mistborn")
        assert result == "_soft_blocked"


class TestBibliographyEmptyInputs:
    @pytest.mark.asyncio
    async def test_missing_author_id_returns_none_no_http(
        self, isolated_id_cache, stub_session,
    ):
        gb = isolated_id_cache
        stub_session["handler"] = lambda url: pytest.fail(
            "should not hit HTTP when author_id is empty"
        )
        assert await gb.find_book_in_bibliography("", "Mistborn") is None

    @pytest.mark.asyncio
    async def test_missing_title_returns_none_no_http(
        self, isolated_id_cache, stub_session,
    ):
        gb = isolated_id_cache
        stub_session["handler"] = lambda url: pytest.fail(
            "should not hit HTTP when title is empty"
        )
        assert await gb.find_book_in_bibliography("38550", "") is None
