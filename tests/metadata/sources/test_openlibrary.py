"""
Tests for the v2.11.0 per-book OpenLibrarySource (enricher path).

Distinct from `tests/discovery/sources/test_openlibrary.py`, which
covers the per-author discovery scanner. Per-book is title+author
keyed via `/search.json` + ISBN-keyed via `/api/books?bibkeys=…`,
and returns a `MetaRecord` (not an `AuthorResult`).

Uses `httpx.MockTransport` because `MetaSource` clients are
stable across calls (unlike `BaseSource` which rebuilds on every
property access).
"""
from __future__ import annotations

import json

import httpx

from app.metadata.sources.openlibrary import (
    OpenLibrarySource,
    _first_known_language,
    _parse_series_from_title,
    _strip_series_suffix,
)


def _make_source(handler) -> OpenLibrarySource:
    src = OpenLibrarySource(rate_limit=0)
    src.set_client(httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    ))
    return src


# ── Pure helpers ──────────────────────────────────────────────────────


class TestSeriesExtraction:
    def test_with_index(self):
        n, i = _parse_series_from_title("Words of Radiance (Stormlight Archive, #2)")
        assert n == "Stormlight Archive"
        assert i == 2.0

    def test_without_index(self):
        n, i = _parse_series_from_title("Some Anthology (Cosmere)")
        assert n == "Cosmere"
        assert i is None

    def test_no_series(self):
        n, i = _parse_series_from_title("Just A Title")
        assert n is None
        assert i is None

    def test_empty(self):
        n, i = _parse_series_from_title("")
        assert n is None
        assert i is None

    def test_strip_series_suffix(self):
        assert _strip_series_suffix(
            "Words of Radiance (Stormlight Archive, #2)"
        ) == "Words of Radiance"

    def test_strip_passthrough_when_no_pattern(self):
        assert _strip_series_suffix("Just A Title") == "Just A Title"


class TestLanguageMap:
    def test_eng_to_en(self):
        assert _first_known_language(["eng"]) == "en"

    def test_marc_key_path_form(self):
        # OL search returns language as e.g. [{"key": "/languages/jpn"}]
        assert _first_known_language([{"key": "/languages/jpn"}]) == "ja"

    def test_unknown_code_returns_none(self):
        assert _first_known_language(["xyz"]) is None

    def test_first_known_wins_over_later_unknown(self):
        assert _first_known_language(["xyz", "fre"]) == "fr"

    def test_empty_list(self):
        assert _first_known_language([]) is None


# ── End-to-end search_book ────────────────────────────────────────────


_SAMPLE_SEARCH_RESPONSE = {
    "docs": [
        {
            "key": "/works/OL15161W",
            "title": "The Way of Kings (Stormlight Archive, #1)",
            "author_name": ["Brandon Sanderson"],
            "cover_i": 12345,
            "publisher": ["Tor"],
            "first_publish_year": 2010,
            "isbn": ["9780765326355", "0765326353"],
            "number_of_pages_median": 1007,
            "language": ["eng"],
            "subject": ["Fantasy", "Epic"],
        },
        {
            "key": "/works/UNRELATED",
            "title": "Way of Drift (totally different)",
            "author_name": ["Some Other Person"],
        },
    ],
}


class TestSearchBookTitleAuthor:
    async def test_assembles_metarecord(self):
        def handler(req):
            assert "search.json" in str(req.url)
            assert req.url.params.get("title") == "The Way of Kings"
            assert req.url.params.get("author") == "Brandon Sanderson"
            return httpx.Response(
                200, content=json.dumps(_SAMPLE_SEARCH_RESPONSE).encode(),
                headers={"Content-Type": "application/json"},
            )

        src = _make_source(handler)
        rec = await src.search_book("The Way of Kings", "Brandon Sanderson")

        assert rec is not None
        assert rec.title == "The Way of Kings"  # series suffix stripped
        assert rec.authors == ["Brandon Sanderson"]
        assert rec.series == "Stormlight Archive"
        assert rec.series_index == 1.0
        assert rec.cover_url == "https://covers.openlibrary.org/b/id/12345-L.jpg"
        assert rec.publisher == "Tor"
        assert rec.pub_date == "2010"
        assert rec.isbn == "9780765326355"
        assert rec.page_count == 1007
        assert rec.language == "en"
        assert "Fantasy" in rec.tags
        assert rec.source == "openlibrary"
        assert rec.external_id == "OL15161W"
        await src.close()

    async def test_empty_title_returns_none(self):
        called = {"n": 0}

        def handler(req):
            called["n"] += 1
            return httpx.Response(200, content=b"{}")

        src = _make_source(handler)
        rec = await src.search_book("", "any author")
        assert rec is None
        assert called["n"] == 0, "must not hit network for empty title"
        await src.close()

    async def test_no_docs_returns_none(self):
        def handler(req):
            return httpx.Response(
                200, content=json.dumps({"docs": []}).encode(),
                headers={"Content-Type": "application/json"},
            )

        src = _make_source(handler)
        rec = await src.search_book("Nonexistent", "Nobody")
        assert rec is None
        await src.close()

    async def test_low_score_match_rejected(self):
        # Top hit has a wildly different title+author — score should
        # fall below the 0.3 floor and the source returns None.
        def handler(req):
            return httpx.Response(
                200, content=json.dumps({
                    "docs": [
                        {
                            "key": "/works/UNREL",
                            "title": "Completely Unrelated Cookbook",
                            "author_name": ["Some Chef"],
                        },
                    ],
                }).encode(),
                headers={"Content-Type": "application/json"},
            )

        src = _make_source(handler)
        rec = await src.search_book("The Way of Kings", "Brandon Sanderson")
        assert rec is None
        await src.close()

    async def test_http_error_returns_none(self, monkeypatch):
        def handler(req):
            return httpx.Response(500, content=b"")

        # Skip retry-backoff sleeps so the test runs fast
        import asyncio as _aio
        async def _no_sleep(_):
            return None
        monkeypatch.setattr(_aio, "sleep", _no_sleep)

        src = _make_source(handler)
        rec = await src.search_book("any title", "any author")
        assert rec is None  # exception caught → None
        await src.close()

    async def test_no_series_in_title_passthrough(self):
        def handler(req):
            return httpx.Response(
                200, content=json.dumps({
                    "docs": [{
                        "key": "/works/OL68428W",
                        "title": "Elantris",
                        "author_name": ["Brandon Sanderson"],
                        "first_publish_year": 2005,
                    }],
                }).encode(),
                headers={"Content-Type": "application/json"},
            )

        src = _make_source(handler)
        rec = await src.search_book("Elantris", "Brandon Sanderson")
        assert rec is not None
        assert rec.title == "Elantris"
        assert rec.series is None
        assert rec.series_index is None
        await src.close()


# ── ISBN-keyed search_by_isbn ─────────────────────────────────────────


_SAMPLE_BIBKEYS_RESPONSE = {
    "ISBN:9780765326355": {
        "title": "The Way of Kings",
        "url": "https://openlibrary.org/books/OL24230520M/The_Way_of_Kings",
        "authors": [{"name": "Brandon Sanderson", "url": "..."}],
        "cover": {
            "small": "https://covers.openlibrary.org/b/id/12345-S.jpg",
            "medium": "https://covers.openlibrary.org/b/id/12345-M.jpg",
            "large": "https://covers.openlibrary.org/b/id/12345-L.jpg",
        },
        "publishers": [{"name": "Tor Books"}],
        "publish_date": "August 31, 2010",
        "number_of_pages": 1007,
        "subjects": [
            {"name": "Epic fantasy", "url": "..."},
            {"name": "Magic", "url": "..."},
        ],
        "excerpts": [{"text": "Roshar epic begins here.", "comment": "intro"}],
    },
}


class TestSearchByIsbn:
    async def test_isbn_returns_full_record(self):
        def handler(req):
            assert "/api/books" in str(req.url)
            assert req.url.params.get("bibkeys") == "ISBN:9780765326355"
            assert req.url.params.get("jscmd") == "data"
            return httpx.Response(
                200, content=json.dumps(_SAMPLE_BIBKEYS_RESPONSE).encode(),
                headers={"Content-Type": "application/json"},
            )

        src = _make_source(handler)
        rec = await src.search_by_isbn("9780765326355")

        assert rec is not None
        assert rec.title == "The Way of Kings"
        assert rec.authors == ["Brandon Sanderson"]
        assert rec.publisher == "Tor Books"
        assert rec.pub_date == "August 31, 2010"
        assert rec.page_count == 1007
        assert rec.cover_url == "https://covers.openlibrary.org/b/id/12345-L.jpg"
        assert "Epic fantasy" in rec.tags
        assert rec.description == "Roshar epic begins here."
        assert rec.isbn == "9780765326355"
        assert rec.source == "openlibrary"
        await src.close()

    async def test_isbn_dashes_normalized(self):
        # ISBN with embedded dashes should normalize before being keyed
        def handler(req):
            # The normalized form (no dashes) is what should hit the wire
            assert req.url.params.get("bibkeys") == "ISBN:9780765326355"
            return httpx.Response(
                200, content=json.dumps(_SAMPLE_BIBKEYS_RESPONSE).encode(),
                headers={"Content-Type": "application/json"},
            )

        src = _make_source(handler)
        rec = await src.search_by_isbn("978-0-7653-2635-5")
        assert rec is not None
        await src.close()

    async def test_isbn_no_payload_returns_none(self):
        def handler(req):
            # OL returns an empty dict when ISBN unknown
            return httpx.Response(
                200, content=b"{}",
                headers={"Content-Type": "application/json"},
            )

        src = _make_source(handler)
        rec = await src.search_by_isbn("0000000000000")
        assert rec is None
        await src.close()

    async def test_empty_isbn_returns_none(self):
        called = {"n": 0}

        def handler(req):
            called["n"] += 1
            return httpx.Response(200, content=b"{}")

        src = _make_source(handler)
        rec = await src.search_by_isbn("")
        assert rec is None
        assert called["n"] == 0, "must not hit network for empty ISBN"
        await src.close()


# ── Enricher registry integration ─────────────────────────────────────


class TestRegistryIntegration:
    """Confirms OpenLibrarySource is wired into the enricher's
    _SOURCE_REGISTRY so settings-driven priority lists with
    'openlibrary' can instantiate it."""

    def test_registered_in_source_registry(self):
        from app.metadata.enricher import _SOURCE_REGISTRY
        assert "openlibrary" in _SOURCE_REGISTRY
        assert _SOURCE_REGISTRY["openlibrary"] is OpenLibrarySource

    def test_name_attribute_matches_registry_key(self):
        assert OpenLibrarySource.name == "openlibrary"
