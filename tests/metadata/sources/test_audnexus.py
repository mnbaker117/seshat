"""
Audnexus source tests.

Audnexus is the ASIN → metadata hop. We cover the pure translator
function (`_item_to_record`) with a canned payload, the ASIN
normalizer, and the async `fetch_by_asin` end-to-end via a mocked
httpx client.
"""
from __future__ import annotations

import httpx
import pytest


# ─── Pure helpers ─────────────────────────────────────────────

class TestNormalizeAsin:
    def test_valid_upper(self):
        from app.metadata.sources.audnexus import _normalize_asin
        assert _normalize_asin("B00BPVBI4A") == "B00BPVBI4A"

    def test_lower_is_upcased(self):
        from app.metadata.sources.audnexus import _normalize_asin
        assert _normalize_asin("b00bpvbi4a") == "B00BPVBI4A"

    def test_stripped_whitespace(self):
        from app.metadata.sources.audnexus import _normalize_asin
        assert _normalize_asin("  B00BPVBI4A  ") == "B00BPVBI4A"

    def test_non_asin_returns_empty(self):
        from app.metadata.sources.audnexus import _normalize_asin
        assert _normalize_asin("hello") == ""
        assert _normalize_asin("9780765311788") == ""  # ISBN-13
        assert _normalize_asin("") == ""
        # Doesn't start with B
        assert _normalize_asin("A00BPVBI4A") == ""


class TestItemToRecord:
    def test_flattens_audnexus_payload(self):
        from app.metadata.sources.audnexus import _item_to_record

        payload = {
            "asin": "B0041KLD5I",
            "title": "The Final Empire",
            "subtitle": "Mistborn, Book 1",
            "authors": [{"name": "Brandon Sanderson"}],
            "narrators": [{"name": "Michael Kramer"}, {"name": "Kate Reading"}],
            "publisherName": "Macmillan Audio",
            "summary": "A dark fantasy ...",
            "releaseDate": "2006-07-25",
            "image": "https://example.com/cover.jpg",
            "genres": [
                {"name": "Fantasy", "type": "genre"},
                {"name": "Epic", "type": "tag"},
            ],
            "seriesPrimary": {"name": "Mistborn", "position": "1"},
            "language": "english",
            "runtimeLengthMin": 1498,
            "formatType": "unabridged",
            "isbn": "9780765311788",
        }
        rec = _item_to_record(payload, region="us")

        assert rec.title == "The Final Empire"
        assert rec.authors == ["Brandon Sanderson"]
        assert rec.narrator == "Michael Kramer, Kate Reading"
        assert rec.series == "Mistborn"
        assert rec.series_index == 1.0
        assert rec.asin == "B0041KLD5I"
        assert rec.isbn == "9780765311788"
        assert rec.publisher == "Macmillan Audio"
        assert rec.pub_date == "2006"  # year only
        assert rec.duration_sec == 1498 * 60.0
        assert rec.abridged is False
        assert rec.language == "English"  # titlecased
        assert "Fantasy" in rec.tags
        assert "Epic" in rec.tags
        assert rec.cover_url == "https://example.com/cover.jpg"
        assert rec.source == "audnexus"
        assert rec.external_id == "B0041KLD5I"
        assert "audible.com/pd/B0041KLD5I" in rec.source_url

    def test_abridged_true(self):
        from app.metadata.sources.audnexus import _item_to_record
        rec = _item_to_record({
            "asin": "B00AA", "title": "T", "authors": [{"name": "A"}],
            "formatType": "abridged",
        })
        assert rec.abridged is True

    def test_series_index_parsed_from_decimal_string(self):
        from app.metadata.sources.audnexus import _item_to_record
        rec = _item_to_record({
            "asin": "X", "title": "T", "authors": [{"name": "A"}],
            "seriesPrimary": {"name": "Series", "position": "1.5"},
        })
        assert rec.series_index == 1.5

    def test_series_index_dramatized_suffix(self):
        """ABS's cleanSeriesSequence: pull the first numeric run."""
        from app.metadata.sources.audnexus import _item_to_record
        rec = _item_to_record({
            "asin": "X", "title": "T", "authors": [{"name": "A"}],
            "seriesPrimary": {"name": "S", "position": "2, Dramatized Adaptation"},
        })
        assert rec.series_index == 2.0

    def test_runtime_none_leaves_duration_none(self):
        from app.metadata.sources.audnexus import _item_to_record
        rec = _item_to_record({
            "asin": "X", "title": "T", "authors": [{"name": "A"}],
        })
        assert rec.duration_sec is None


# ─── Async fetch_by_asin ──────────────────────────────────────

def _inject_transport(monkeypatch, handler):
    orig = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: orig(
            transport=httpx.MockTransport(handler),
            **{k: v for k, v in kw.items() if k != "transport"},
        ),
    )


class TestFetchByAsin:
    async def test_returns_record_with_confidence_1(self, monkeypatch):
        from app.metadata.sources.audnexus import AudnexusSource

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/books/B0041KLD5I" in request.url.path
            assert request.url.params.get("region") == "us"
            return httpx.Response(200, json={
                "asin": "B0041KLD5I",
                "title": "The Final Empire",
                "authors": [{"name": "Brandon Sanderson"}],
            })

        _inject_transport(monkeypatch, handler)
        src = AudnexusSource(region="us", rate_limit=0)
        rec = await src.fetch_by_asin("B0041KLD5I")
        assert rec is not None
        assert rec.confidence == 1.0
        assert rec.title == "The Final Empire"

    async def test_invalid_asin_returns_none_without_http(self, monkeypatch):
        from app.metadata.sources.audnexus import AudnexusSource

        calls: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json={})

        _inject_transport(monkeypatch, handler)
        src = AudnexusSource(rate_limit=0)
        assert await src.fetch_by_asin("bogus") is None
        assert calls == []  # no HTTP call made for malformed ASIN

    async def test_missing_asin_in_response_returns_none(self, monkeypatch):
        from app.metadata.sources.audnexus import AudnexusSource

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "not found"})

        _inject_transport(monkeypatch, handler)
        src = AudnexusSource(rate_limit=0)
        assert await src.fetch_by_asin("B00FAKE0001") is None

    async def test_http_failure_returns_none(self, monkeypatch):
        from app.metadata.sources.audnexus import AudnexusSource

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        _inject_transport(monkeypatch, handler)
        src = AudnexusSource(rate_limit=0)
        assert await src.fetch_by_asin("B00FAKE0001") is None

    async def test_search_book_always_returns_none(self):
        """Audnexus has no title/author search — see module docstring."""
        from app.metadata.sources.audnexus import AudnexusSource
        src = AudnexusSource(rate_limit=0)
        assert await src.search_book("Some Title", "Some Author") is None

    def test_region_defaults_when_unknown(self):
        from app.metadata.sources.audnexus import AudnexusSource
        assert AudnexusSource(region="zz").region == "us"
