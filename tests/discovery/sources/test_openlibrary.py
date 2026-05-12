"""
Tests for the v2.10.6 OpenLibrarySource.

Mirrors the Hardcover test pattern: monkeypatch HTTP at the
`_get` level since `BaseSource._get_client` rebuilds the client
on every property access (which clobbers any MockTransport
injected on `self._client`).

Coverage:
  - Series-from-title extractor edge cases
  - `_resolve_author_key` disambiguation (single, multi w/ name match,
    multi w/ work_count tiebreak, no match → fallback to top hit)
  - `_fetch_all_author_works` pagination (single page, multi-page,
    partial-page stop)
  - End-to-end `search_author` (assembled AuthorResult, no-author miss)
  - `get_author_books` is a no-op stub (mirrors HardcoverSource pattern)
"""
from __future__ import annotations

import json

import httpx

from app.discovery.sources.openlibrary import (
    OpenLibrarySource,
    _extract_series_from_title,
)


def _patch_get(src: OpenLibrarySource, responses: dict, *, call_log: list = None):
    """Patch `_get` to map URL substrings to canned httpx.Responses.

    `responses` keys are URL substrings (e.g. "search/authors.json",
    "/authors/OL26320A/works.json"). Values are either:
      - a single dict (returned as JSON every time the URL matches)
      - a list of dicts (returned in order, one per call — for pagination tests)
    """
    state = {k: 0 for k in responses}

    async def fake_get(url: str, retries: int = 2, **kwargs):  # noqa: ARG001
        for url_frag, payload in responses.items():
            if url_frag in url:
                if call_log is not None:
                    call_log.append((url_frag, dict(kwargs.get("params") or {})))
                if isinstance(payload, list):
                    idx = state[url_frag]
                    state[url_frag] = idx + 1
                    body = payload[idx] if idx < len(payload) else {}
                else:
                    body = payload
                return httpx.Response(
                    200, content=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                )
        if call_log is not None:
            call_log.append(("unmatched", url))
        return httpx.Response(404, content=b"")

    src._get = fake_get  # type: ignore[method-assign]


def _make_source() -> OpenLibrarySource:
    return OpenLibrarySource(rate_limit=0)


# ── Pure helper: series extraction from title ─────────────────────


class TestSeriesExtraction:
    def test_simple_series_with_index(self):
        s, idx, cleaned = _extract_series_from_title("The Way of Kings (Stormlight Archive, #1)")
        assert s == "Stormlight Archive"
        assert idx == 1.0
        assert cleaned == "The Way of Kings"

    def test_series_with_decimal_index(self):
        s, idx, cleaned = _extract_series_from_title("Brief Cases (Dresden Files #15.6)")
        assert s == "Dresden Files"
        assert idx == 15.6
        assert cleaned == "Brief Cases"

    def test_series_without_index(self):
        s, idx, cleaned = _extract_series_from_title("Some Anthology (Stormlight Archive)")
        assert s == "Stormlight Archive"
        assert idx is None
        assert cleaned == "Some Anthology"

    def test_no_series_returns_unchanged(self):
        s, idx, cleaned = _extract_series_from_title("Just A Title")
        assert s is None
        assert idx is None
        assert cleaned == "Just A Title"

    def test_empty_title_safe(self):
        s, idx, cleaned = _extract_series_from_title("")
        assert s is None
        assert idx is None
        assert cleaned == ""


# ── Phase 1: author-key resolution ────────────────────────────────


class TestResolveAuthorKey:
    async def test_single_match_returned_directly(self):
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {
                "docs": [{"key": "/authors/OL38550A", "name": "Brandon Sanderson", "work_count": 142}],
            },
        })

        result = await src._resolve_author_key("Brandon Sanderson")

        assert result == "/authors/OL38550A"
        await src.close()

    async def test_strict_name_match_wins_over_partial(self):
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {
                "docs": [
                    {"key": "/authors/OL1A", "name": "Brandon Sanderson, Jr.", "work_count": 5},
                    {"key": "/authors/OL2A", "name": "Brandon Sanderson", "work_count": 142},
                    {"key": "/authors/OL3A", "name": "Brandon Sandersonish", "work_count": 100},
                ],
            },
        })

        result = await src._resolve_author_key("Brandon Sanderson")

        assert result == "/authors/OL2A"
        await src.close()

    async def test_work_count_breaks_tie_on_strict_match(self):
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {
                "docs": [
                    {"key": "/authors/OL10A", "name": "John Smith", "work_count": 5},
                    {"key": "/authors/OL20A", "name": "John Smith", "work_count": 200},
                    {"key": "/authors/OL30A", "name": "John Smith", "work_count": 50},
                ],
            },
        })

        result = await src._resolve_author_key("John Smith")

        assert result == "/authors/OL20A"  # most prolific Smith wins
        await src.close()

    async def test_no_strict_match_falls_back_to_top_hit(self):
        # OL returned candidates but none pass the strict name-match
        # gate (e.g., all are partial/wildly-different matches). Fall
        # back to OL's top-ranked hit rather than returning None.
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {
                "docs": [
                    {"key": "/authors/OL99A", "name": "Wildly Different Person", "work_count": 50},
                    {"key": "/authors/OL98A", "name": "Also Different Author", "work_count": 1},
                ],
            },
        })

        result = await src._resolve_author_key("Brandon Sanderson")

        assert result == "/authors/OL99A"  # top-ranked OL hit
        await src.close()

    async def test_no_results_returns_none(self):
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {"docs": []},
        })

        result = await src._resolve_author_key("Nonexistent Author")

        assert result is None
        await src.close()

    async def test_period_normalization_matches(self):
        # "J.N. Chaney" (no space) should match "J. N. Chaney"
        # (space-separated) — same trick as the v2.10.5 Hardcover fix.
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {
                "docs": [{"key": "/authors/OL777A", "name": "J.N. Chaney", "work_count": 398}],
            },
        })

        result = await src._resolve_author_key("J. N. Chaney")

        assert result == "/authors/OL777A"
        await src.close()


# ── Phase 2: paginated works walk ─────────────────────────────────


class TestFetchAllAuthorWorks:
    async def test_single_page_under_limit(self):
        src = _make_source()
        call_log: list = []
        _patch_get(src, {
            "/authors/OL38550A/works.json": {
                "entries": [{"key": f"/works/OL{i}W", "title": f"Book {i}"} for i in range(10)],
            },
        }, call_log=call_log)

        works = await src._fetch_all_author_works("/authors/OL38550A")

        assert len(works) == 10
        assert len(call_log) == 1  # no pagination
        assert works[0]["title"] == "Book 0"
        await src.close()

    async def test_paginates_through_full_pages(self):
        # 100 + 100 + 23 = 223 total. Caller should make 3 round-trips
        # and stop when the third page returns < limit rows.
        src = _make_source()
        responses = [
            {"entries": [{"key": f"/works/OL{i}W", "title": f"P1-{i}"} for i in range(100)]},
            {"entries": [{"key": f"/works/OL{100+i}W", "title": f"P2-{i}"} for i in range(100)]},
            {"entries": [{"key": f"/works/OL{200+i}W", "title": f"P3-{i}"} for i in range(23)]},
        ]
        call_log: list = []
        _patch_get(src, {
            "/authors/OL38550A/works.json": responses,
        }, call_log=call_log)

        works = await src._fetch_all_author_works("/authors/OL38550A")

        assert len(works) == 223
        assert len(call_log) == 3
        offsets = [params.get("offset") for _, params in call_log]
        assert offsets == [0, 100, 200]
        await src.close()

    async def test_bare_key_works_too(self):
        # `_fetch_all_author_works` should accept either "/authors/OL38550A"
        # or just "OL38550A" — it strips the prefix internally.
        src = _make_source()
        call_log: list = []
        _patch_get(src, {
            "/authors/OL38550A/works.json": {"entries": []},
        }, call_log=call_log)

        await src._fetch_all_author_works("OL38550A")

        assert len(call_log) == 1
        await src.close()

    async def test_no_entries_returns_empty(self):
        src = _make_source()
        _patch_get(src, {
            "/authors/OL38550A/works.json": {"entries": []},
        })

        works = await src._fetch_all_author_works("/authors/OL38550A")

        assert works == []
        await src.close()


# ── End-to-end search_author ──────────────────────────────────────


class TestSearchAuthorEndToEnd:
    async def test_assembles_books_and_series(self):
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {
                "docs": [{"key": "/authors/OL38550A", "name": "Brandon Sanderson", "work_count": 142}],
            },
            "/authors/OL38550A/works.json": {
                "entries": [
                    {
                        "key": "/works/OL15161W",
                        "title": "The Way of Kings (The Stormlight Archive, #1)",
                        "covers": [12345],
                        "description": "Roshar epic.",
                        "first_publish_date": "2010-08-31",
                    },
                    {
                        "key": "/works/OL26321W",
                        "title": "Words of Radiance (The Stormlight Archive, #2)",
                        "covers": [67890],
                        "description": {"type": "/type/text", "value": "Continuation."},
                        "first_publish_date": "2014-03-04",
                    },
                    {
                        "key": "/works/OL68428W",
                        "title": "Elantris",
                        "covers": [],
                        "first_publish_date": "2005-04-21",
                    },
                ],
            },
        })

        result = await src.search_author("Brandon Sanderson")

        assert result is not None
        assert result.external_id == "/authors/OL38550A"
        assert len(result.books) == 1  # Elantris (standalone)
        assert len(result.series) == 1  # Stormlight Archive
        sa = result.series[0]
        assert sa.name == "The Stormlight Archive"
        assert len(sa.books) == 2
        assert sa.books[0].title == "The Way of Kings"
        assert sa.books[0].series_index == 1.0
        assert sa.books[0].cover_url == "https://covers.openlibrary.org/b/id/12345-L.jpg"
        assert sa.books[1].title == "Words of Radiance"
        assert sa.books[1].description == "Continuation."
        assert result.books[0].title == "Elantris"
        assert result.books[0].cover_url is None  # no covers
        await src.close()

    async def test_no_author_match_returns_none(self):
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {"docs": []},
        })

        result = await src.search_author("Nonexistent Author")

        assert result is None
        await src.close()

    async def test_author_found_but_zero_works_returns_minimal_result(self):
        # OL has the author record but their works list is empty.
        # Return a populated AuthorResult anyway so downstream knows
        # the author was resolved (just no books to merge).
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {
                "docs": [{"key": "/authors/OLNEWA", "name": "New Author", "work_count": 0}],
            },
            "/authors/OLNEWA/works.json": {"entries": []},
        })

        result = await src.search_author("New Author")

        assert result is not None
        assert result.external_id == "/authors/OLNEWA"
        assert result.books == []
        assert result.series == []
        await src.close()

    async def test_get_author_books_is_no_op(self):
        # Mirrors HardcoverSource: search_author already returns
        # the full result, so get_author_books returns None and the
        # caller's two-phase flow collapses to phase 1.
        src = _make_source()
        result = await src.get_author_books("/authors/OL38550A")
        assert result is None
        await src.close()

    async def test_format_specific_parenthetical_not_treated_as_series(self):
        # "(Annotated)", "(2nd Edition)", "(Illustrated)", "(Boxed Set)"
        # are common OL title decorations and must NOT become series_name.
        # Both the exact-name reject list AND the "Nth Edition" regex
        # have to fire correctly.
        src = _make_source()
        _patch_get(src, {
            "search/authors.json": {
                "docs": [{"key": "/authors/OL1A", "name": "Some Author", "work_count": 5}],
            },
            "/authors/OL1A/works.json": {
                "entries": [
                    {"key": "/works/OL1W", "title": "Some Book (Annotated)"},
                    {"key": "/works/OL2W", "title": "Some Book (2nd Edition)"},
                    {"key": "/works/OL3W", "title": "Some Book (Illustrated)"},
                    {"key": "/works/OL4W", "title": "Some Book (10th Edition)"},
                    {"key": "/works/OL5W", "title": "Some Book (Boxed Set)"},
                ],
            },
        })

        result = await src.search_author("Some Author")

        assert result is not None
        assert len(result.series) == 0, (
            f"format/edition decorations leaked into series_map: "
            f"{[s.name for s in result.series]}"
        )
        assert len(result.books) == 5
        for b in result.books:
            assert b.series_name is None
            # And the cleaned title should retain the original — we
            # didn't strip the decoration, we just refused to read it
            # as series info.
            assert b.title.startswith("Some Book")
        await src.close()
