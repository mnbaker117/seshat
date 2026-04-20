"""
Audible source tests.

Audible catalog search → Audnexus hydration. Tests mock the entire
HTTP layer via `httpx.MockTransport`. The transport dispatches by
URL host so catalog and Audnexus hits get independent responses.
"""
from __future__ import annotations

import httpx


def _inject_transport(monkeypatch, handler):
    """Rebind httpx.AsyncClient so every client this test creates
    uses the given handler. We do it this way rather than passing
    the transport in directly because AudibleSource constructs its
    own httpx clients internally (via the base MetaSource)."""
    orig = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: orig(
            transport=httpx.MockTransport(handler),
            **{k: v for k, v in kw.items() if k != "transport"},
        ),
    )


def _make_handler(catalog_hits: list[dict], audnexus_items: dict[str, dict]):
    """Build a router-style handler.

    - Any request to a host starting with `api.audible` returns
      `{"products": catalog_hits}`.
    - Any request matching `/books/{asin}` on `api.audnex.us`
      returns `audnexus_items[asin]` (or 404 if missing).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host.startswith("api.audible"):
            return httpx.Response(200, json={"products": catalog_hits})
        if host == "api.audnex.us":
            path = request.url.path
            if path.startswith("/books/"):
                asin = path.rsplit("/", 1)[-1]
                item = audnexus_items.get(asin)
                if item is None:
                    return httpx.Response(404, json={"error": "not found"})
                return httpx.Response(200, json=item)
        return httpx.Response(404)
    return handler


class TestAudibleSearch:
    async def test_catalog_search_hydrates_via_audnexus(self, monkeypatch):
        from app.metadata.sources.audible import AudibleSource

        handler = _make_handler(
            catalog_hits=[
                {"asin": "B00WRONG001", "title": "Wrong Book"},
                {"asin": "B0041KLD5I", "title": "The Final Empire"},
            ],
            audnexus_items={
                "B00WRONG001": {
                    "asin": "B00WRONG001", "title": "Wrong Book",
                    "authors": [{"name": "Other Author"}],
                },
                "B0041KLD5I": {
                    "asin": "B0041KLD5I", "title": "The Final Empire",
                    "authors": [{"name": "Brandon Sanderson"}],
                    "narrators": [{"name": "Michael Kramer"}],
                    "runtimeLengthMin": 1498,
                },
            },
        )
        _inject_transport(monkeypatch, handler)

        src = AudibleSource(region="us", rate_limit=0)
        rec = await src.search_book("The Final Empire", "Brandon Sanderson")

        assert rec is not None
        assert rec.title == "The Final Empire"
        assert rec.authors == ["Brandon Sanderson"]
        assert rec.narrator == "Michael Kramer"
        assert rec.source == "audible"
        # Confidence is the scored similarity, not the Audnexus 1.0.
        assert 0.3 < rec.confidence <= 1.0

    async def test_ascii_title_that_looks_like_asin_shortcut(self, monkeypatch):
        """A title that IS an ASIN should bypass the catalog search."""
        from app.metadata.sources.audible import AudibleSource

        catalog_calls: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host.startswith("api.audible"):
                catalog_calls.append(request.url.path)
                return httpx.Response(200, json={"products": []})
            if request.url.host == "api.audnex.us":
                return httpx.Response(200, json={
                    "asin": "B0041KLD5I", "title": "TFE",
                    "authors": [{"name": "BS"}],
                })
            return httpx.Response(404)

        _inject_transport(monkeypatch, handler)
        src = AudibleSource(region="us", rate_limit=0)
        rec = await src.search_book("B0041KLD5I", "")
        assert rec is not None
        assert rec.asin == "B0041KLD5I"
        assert catalog_calls == []  # catalog never touched

    async def test_empty_catalog_returns_none(self, monkeypatch):
        from app.metadata.sources.audible import AudibleSource
        _inject_transport(monkeypatch, _make_handler([], {}))
        src = AudibleSource(rate_limit=0)
        assert await src.search_book("Obscure Title", "Nobody") is None

    async def test_low_score_returns_none(self, monkeypatch):
        """Hits that don't match title/author well enough are rejected."""
        from app.metadata.sources.audible import AudibleSource

        handler = _make_handler(
            catalog_hits=[{"asin": "B0WRONG0001"}],
            audnexus_items={
                "B0WRONG0001": {
                    "asin": "B0WRONG0001",
                    "title": "Totally Unrelated Work",
                    "authors": [{"name": "Nobody Familiar"}],
                },
            },
        )
        _inject_transport(monkeypatch, handler)
        src = AudibleSource(rate_limit=0)
        assert await src.search_book("Looking For Something Else",
                                     "Different Author") is None

    async def test_empty_title_returns_none(self):
        from app.metadata.sources.audible import AudibleSource
        src = AudibleSource(rate_limit=0)
        assert await src.search_book("", "") is None

    def test_region_falls_back_to_us(self):
        from app.metadata.sources.audible import AudibleSource
        assert AudibleSource(region="zz").region == "us"

    def test_region_tld_mapping(self):
        from app.metadata.sources.audible import AudibleSource, REGION_TLDS
        src = AudibleSource(region="uk")
        assert src._catalog_url().endswith(".co.uk/1.0/catalog/products")
        assert REGION_TLDS["de"] == ".de"
