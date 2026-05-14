"""
Tests for the Amazon /juvec POST client
(v2.11.0 Stage 5++ commit 4/6).

JuvecClient builds two request shapes (filter-application,
detail-fetch) from the page data + makes them through an injected
async session. These tests cover body construction (the captured
cURL bodies in `tests/fixtures/amazon/` are ground truth), transport
plumbing (retries, thin-body guard, JSON parse failure), and burst
throttling.

The /juvec response shape is parsed via the parser from commit 2;
we use the parser's own `parse_juvec_response` test coverage as the
authority on response handling. Here we just verify the client
plumbs the bytes through.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.discovery.sources.amazon_juvec_client import (
    JuvecClient,
    JuvecError,
)
from app.discovery.sources.amazon_widget_parser import AllBooksPageData


# ─── Mock session/response ──────────────────────────────────────


class MockResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class MockSession:
    """Records every POST. Supports a sequence of response stubs so
    we can test retry logic — pop responses in order."""

    def __init__(self, responses: list[MockResponse] | None = None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url: str, json: dict, timeout: float = 30.0):
        self.calls.append((url, json))
        if not self.responses:
            raise AssertionError("MockSession ran out of stubbed responses")
        return self.responses.pop(0)


# ─── Fixture: typical AllBooksPageData ──────────────────────────


@pytest.fixture
def page_data():
    return AllBooksPageData(
        author_id="B001IGFHW6",
        store_id="264b6c18-49aa-3168-afdd-309475c0e555",
        root_page_id="e404fdad-587a-39a3-b453-d17a440bb311",
        version="264b6c18-49aa-3168-afdd-309475c0e555",
        slate_token="SLATE_TOKEN_OPAQUE_ABC123",
        fresh_cart_csrf_token="FRESHCART_TOKEN_DEF456",
        amazon_api_csrf_token="1@APICSRF_GHI789",
        visit_id="4340eb99-d3b7-4f71-a1eb-38a1909af350",
        obfuscated_marketplace_id="ATVPDKIKX0DER",
        asin_list=("B002GYI9C4", "B001QKBHG4", "B003P2WO5E"),
        products=(),
        total_result_count=645,
        total_count=645,
        available_languages=("English", "Spanish", "German"),
    )


def _wellformed_response(asin_list: list[str] = None, total: int = 645):
    """Build a /juvec response JSON the parser will accept. Live
    response shape (validated 2026-05-13): products + ASINList +
    totalResultCount at top level, plus a request-echo `content`
    field. Padded to ≥1 KB so the thin-body guard doesn't trip."""
    body = {
        "ASINList": asin_list or [],
        "totalResultCount": total,
        "totalCount": total,
        "isSuccess": True,
        "products": [
            {
                "asin": "B002GYI9C4",
                "title": {"displayString": "Mistborn"},
                "bindingInformation": {
                    "binding": {
                        "symbol": "kindle_edition",
                        "displayString": "Kindle Edition",
                    },
                },
                "byLine": {"contributors": [{"name": "Brandon Sanderson"}]},
                "mediaMatrix": {"items": []},
            },
        ],
        "content": {"includeOutOfStock": True},  # request echo
    }
    raw = json.dumps(body)
    # Pad if too short
    padding = " " * max(0, 1100 - len(raw))
    return MockResponse(200, raw + padding)


# ─── Body construction ──────────────────────────────────────────


class TestFilterApplicationBody:
    """Filter-application request: body shape A from the captured
    cURL (Kindle + English filter just applied)."""

    async def test_includes_author_search_block(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(page=1)

        url, body = session.calls[0]
        assert url == "https://www.amazon.com/juvec"
        assert "authorSearch" in body
        assert body["authorSearch"]["page"] == 1
        assert body["authorSearch"]["pageSize"] == 112
        assert body["authorSearch"]["sort"] == "author-sidecar-rank"

    async def test_omits_asin_list(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(page=1)

        _, body = session.calls[0]
        assert "ASINList" not in body, (
            "filter-application request must NOT carry ASINList — "
            "the server computes the filtered ASINList itself"
        )

    async def test_author_filters_format_and_language(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(
            page=1, format_filter="kindle", language="English",
        )

        _, body = session.calls[0]
        assert body["authorFilters"]["format"] == ["kindle"]
        assert body["authorFilters"]["language"] == ["English"]

    async def test_page_number_threaded_through(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(page=3)

        _, body = session.calls[0]
        assert body["authorSearch"]["page"] == 3

    async def test_unfiltered_format_accepted(self, page_data):
        """When user wants no format filter, send ``allFormats`` to
        match the page's default state (mirrors Amazon's UI dropdown
        labelled 'All Formats')."""
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(
            page=1, format_filter="allFormats", language="All Languages",
        )

        _, body = session.calls[0]
        assert body["authorFilters"]["format"] == ["allFormats"]
        assert body["authorFilters"]["language"] == ["All Languages"]


class TestDetailFetchBody:
    """Detail-fetch request: body shape B from the captured cURL
    (the in-progress batch-hydrate as user scrolled)."""

    async def test_includes_asin_list(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_asin_batch(["B007ECLVT6", "B09PCBDXFB"])

        _, body = session.calls[0]
        assert body["ASINList"] == ["B007ECLVT6", "B09PCBDXFB"]

    async def test_omits_author_search(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_asin_batch(["B007ECLVT6"])

        _, body = session.calls[0]
        assert "authorSearch" not in body, (
            "detail-fetch request must NOT carry authorSearch — only "
            "an explicit ASINList"
        )

    async def test_carries_filter_context(self, page_data):
        """Even detail-fetch carries authorFilters context so the
        server knows which format/language pool the requested ASINs
        belong to."""
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_asin_batch(
            ["B007ECLVT6"], format_filter="kindle", language="English",
        )

        _, body = session.calls[0]
        assert body["authorFilters"]["format"] == ["kindle"]
        assert body["authorFilters"]["language"] == ["English"]

    async def test_empty_asin_list_skips_post(self, page_data):
        """Pre-empty batches shouldn't hit the wire — return empty
        response synthesized client-side."""
        session = MockSession([])  # no responses needed
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        resp = await client.fetch_asin_batch([])
        assert resp.products == ()
        assert session.calls == []

    async def test_oversized_batch_raises(self, page_data):
        """Caller is responsible for chunking; >16 in one call is a
        programming error."""
        session = MockSession([])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        with pytest.raises(ValueError, match="max batch size is 16"):
            await client.fetch_asin_batch(["X" + str(i).zfill(9) for i in range(20)])


class TestSharedBodyFields:
    """Both shapes share requestContext + pageContext blocks."""

    async def test_csrf_tokens_passed_through(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(page=1)

        _, body = session.calls[0]
        ctx = body["requestContext"]
        assert ctx["slateToken"] == "SLATE_TOKEN_OPAQUE_ABC123"
        assert ctx["freshCartCsrfToken"] == "FRESHCART_TOKEN_DEF456"
        assert ctx["amazonApiCsrfToken"] == "1@APICSRF_GHI789"

    async def test_pagecontext_ids_correct(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(page=1)

        _, body = session.calls[0]
        pc = body["pageContext"]
        assert pc["authorId"] == "B001IGFHW6"
        assert pc["storeId"] == "264b6c18-49aa-3168-afdd-309475c0e555"
        assert pc["rootPageId"] == "e404fdad-587a-39a3-b453-d17a440bb311"
        assert pc["version"] == "264b6c18-49aa-3168-afdd-309475c0e555"
        assert pc["pagePath"] == "/author/B001IGFHW6/allbooks"

    async def test_anonymous_customer_fields(self, page_data):
        """Mark's captures had logged-in customerId / customerIP.
        Anonymous server-side scans should send empty strings —
        validated to be acceptable by the probe script."""
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(page=1)

        _, body = session.calls[0]
        ctx = body["requestContext"]
        assert ctx["customerId"] == ""
        assert ctx["customerIP"] == ""
        assert ctx["sessionId"] == ""

    async def test_visit_id_passed_through(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(page=1)

        _, body = session.calls[0]
        assert (
            body["requestContext"]["appendedParameters"]["visitId"]
            == "4340eb99-d3b7-4f71-a1eb-38a1909af350"
        )

    async def test_marketplace_id_passed_through(self, page_data):
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        await client.fetch_filtered_page(page=1)

        _, body = session.calls[0]
        ctx = body["requestContext"]
        assert ctx["obfuscatedMarketplaceId"] == "ATVPDKIKX0DER"
        assert ctx["obfuscatedMerchantId"] == "ATVPDKIKX0DER"


# ─── Transport ──────────────────────────────────────────────────


class TestTransport:
    async def test_200_returns_parsed_response(self, page_data):
        session = MockSession([_wellformed_response(asin_list=["B001X"], total=83)])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        resp = await client.fetch_filtered_page(page=1)
        assert resp.asin_list == ("B001X",)
        assert resp.total_result_count == 83
        assert len(resp.products) == 1

    async def test_5xx_retries(self, page_data):
        """One transient 503 → retry → 200. JuvecError NOT raised."""
        session = MockSession([
            MockResponse(503, ""),
            _wellformed_response(),
        ])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        # Make the retry sleep instantaneous in test
        resp = await client.fetch_filtered_page(page=1)
        assert resp.total_result_count == 645
        assert len(session.calls) == 2  # initial + retry

    async def test_5xx_exhaust_retries_raises(self, page_data):
        """Two 503s in a row exhaust the default 1-retry budget."""
        session = MockSession([
            MockResponse(503, ""),
            MockResponse(503, ""),
        ])
        client = JuvecClient(page_data, session, burst_delay_s=0.0, max_retries=1)
        with pytest.raises(JuvecError, match="HTTP 503"):
            await client.fetch_filtered_page(page=1)

    async def test_4xx_raises_immediately(self, page_data):
        """Client errors (403, 404, 429) don't get retried — they're
        not transient. Raise JuvecError on first hit."""
        session = MockSession([MockResponse(403, "")])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        with pytest.raises(JuvecError, match="HTTP 403"):
            await client.fetch_filtered_page(page=1)

    async def test_thin_body_raises(self, page_data):
        """200 OK with body <1000 chars is Akamai's bot-block
        signature — treat as failure even though status is 200."""
        session = MockSession([MockResponse(200, "<html>blocked</html>")])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        with pytest.raises(JuvecError, match="thin body"):
            await client.fetch_filtered_page(page=1)

    async def test_invalid_json_raises(self, page_data):
        """200 OK with non-JSON body."""
        # Make body large enough to bypass the thin-body guard
        session = MockSession([MockResponse(200, "not json " * 200)])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        with pytest.raises(JuvecError, match="not JSON"):
            await client.fetch_filtered_page(page=1)

    async def test_parse_error_wrapped(self, page_data):
        """200 OK + valid JSON but unrecognised shape — parser
        ParseError gets surfaced as JuvecError."""
        bogus = json.dumps({"totally": "unrelated"})
        # Pad to bypass thin-body guard
        session = MockSession([MockResponse(200, bogus + " " * 1100)])
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        with pytest.raises(JuvecError, match="parse error"):
            await client.fetch_filtered_page(page=1)

    async def test_transport_exception_retries_then_raises(self, page_data):
        """When session.post itself raises, the client retries once
        then raises JuvecError."""

        class FlakySession(MockSession):
            async def post(self, url, json, timeout=30.0):
                self.calls.append((url, json))
                raise ConnectionError("dns busted")

        session = FlakySession()
        client = JuvecClient(page_data, session, burst_delay_s=0.0, max_retries=1)
        with pytest.raises(JuvecError, match="transport error"):
            await client.fetch_filtered_page(page=1)
        assert len(session.calls) == 2  # initial + 1 retry


# ─── Throttling ─────────────────────────────────────────────────


class TestBurstThrottle:
    async def test_first_post_no_delay(self, page_data):
        """The first POST in a scan should fire immediately."""
        session = MockSession([_wellformed_response()])
        client = JuvecClient(page_data, session, burst_delay_s=0.5)
        start = asyncio.get_event_loop().time()
        await client.fetch_filtered_page(page=1)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.4, f"first POST should not throttle, elapsed={elapsed:.3f}"

    async def test_subsequent_post_sleeps(self, page_data):
        """Second POST in the same client instance sleeps at least
        burst_delay_s before firing."""
        session = MockSession([
            _wellformed_response(),
            _wellformed_response(),
        ])
        client = JuvecClient(page_data, session, burst_delay_s=0.3)
        start = asyncio.get_event_loop().time()
        await client.fetch_filtered_page(page=1)
        await client.fetch_asin_batch(["BXXXXXXXXX"])
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed >= 0.3, f"second POST should throttle ≥0.3s, elapsed={elapsed:.3f}"
