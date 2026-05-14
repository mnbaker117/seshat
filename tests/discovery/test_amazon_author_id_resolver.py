"""
Tests for the Amazon Author Store ID resolver
(v2.11.0 Stage 5++ commit 3/6).

The resolver maps an author name (optionally + a known book ASIN) to
the 10-char Amazon Author Store ID (e.g. "B001IGFHW6"). Two tiers:
  - Tier 1: GET /dp/{asin} and extract byLine contributor link
  - Tier 2: GET /s?k=... and disambiguate among author anchors

Both behind curl_cffi; tested here with injected mock sessions so the
test rig stays curl_cffi-free.
"""
from __future__ import annotations

from app.discovery.amazon_author_id_resolver import (
    _extract_author_id_from_html,
    _normalize_name,
    _pick_best_author_id_from_search,
    resolve_amazon_author_id,
)


# ─── Mock session/response objects (curl_cffi-style interface) ──


class MockResponse:
    def __init__(
        self, status_code: int, text: str, url: str | None = None,
    ):
        self.status_code = status_code
        self.text = text
        # When set, simulates curl_cffi's `response.url` carrying the
        # post-redirect target. Tests for the vanity-URL tier set
        # this to mimic Amazon's 301 → /stores/.../author/{id}.
        self.url = url


class MockSession:
    """Minimal async session shim for tests. Maps URL substrings to
    MockResponse instances. Records every get() call for assertions
    about which tier fired."""

    def __init__(self, route_map: dict[str, MockResponse] | None = None):
        self.routes = route_map or {}
        self.calls: list[str] = []
        self.closed = False

    async def get(
        self, url: str, timeout: float = 15.0,
        allow_redirects: bool = True,
    ) -> MockResponse:
        self.calls.append(url)
        for substring, resp in self.routes.items():
            if substring in url:
                # Honour test-supplied `url` on the response (mimics
                # curl_cffi's post-redirect URL). Fall back to the
                # request URL when the test didn't override.
                if resp.url is None:
                    resp.url = url
                return resp
        return MockResponse(status_code=404, text="")

    async def close(self) -> None:
        self.closed = True


# ─── HTML fixture builders ──────────────────────────────────────


def _fat_body(content: str, target: int = 80_000) -> str:
    """Pad HTML to ≥50 KB so the thin-body Akamai-guard doesn't trip
    in tests. Real Amazon pages are 200 KB+; we just need enough."""
    pad = "<!-- " + ("x" * (target - len(content) - 10)) + " -->"
    return content + pad


def _dp_html_with_contributor_path(author_id: str = "B001IGFHW6") -> str:
    """A /dp/{asin} page with the JSON contributor path embedded
    (most common shape — SSR includes the productGrid widget). The
    `/marketplaces/.../authors/{id}` link is the most authoritative
    extraction target."""
    return _fat_body(
        f'<html>...<script>window.bootstrap = {{"product":'
        f'{{"byLine":{{"contributors":[{{"contributor":'
        f'{{"author":"/marketplaces/ATVPDKIKX0DER/contributors/'
        f'authors/{author_id}"}},"name":"Brandon Sanderson"}}]}}'
        f'}}}};</script>...</html>'
    )


def _dp_html_with_anchor_only(author_id: str = "B001IGFHW6") -> str:
    """A /dp/{asin} page without the SSR JSON (older shape, A/B
    bucket, or a minimal detail page). Author ID extractable only
    from anchor href like /-/e/{id} or /Slug/e/{id}."""
    return _fat_body(
        f'<html>...<a class="contributorNameID" '
        f'href="/Brandon-Sanderson/e/{author_id}?ref_=dbs_p_pbk_r00_pieceauthor_0">'
        f'Brandon Sanderson</a>...</html>'
    )


def _search_html(*author_chips: tuple[str, str]) -> str:
    """Build a /s search results page containing book cards whose
    byline anchors point at the given (slug, id) pairs. Each chip
    is rendered twice — once as short form `/-/e/{id}` and once as
    long form `/{slug}/e/{id}` — mirroring Amazon's real markup."""
    parts: list[str] = ['<html><body>']
    for slug, author_id in author_chips:
        parts.append(
            f'<div class="s-result-item">'
            f'<a href="/{slug}/e/{author_id}/ref=sr_aut_dp">{slug.replace("-", " ")}</a>'
            f'<a href="/-/e/{author_id}/ref=sr_aut_alt">.</a>'
            f'</div>'
        )
    parts.append('</body></html>')
    return _fat_body("".join(parts))


# ─── Pure-function tests ────────────────────────────────────────


class TestExtractAuthorIdFromHTML:
    def test_prefers_contributor_path_over_anchor(self):
        """When both shapes are present, the JSON-embedded contributor
        path wins (authoritative; matches the exact ID Amazon uses
        internally even when a redirect slug happens to differ)."""
        html = (
            '<a href="/Brandon-Sanderson/e/BWRONGCODE">link</a>'
            '/marketplaces/ATVPDKIKX0DER/contributors/authors/B001IGFHW6'
        )
        assert _extract_author_id_from_html(html) == "B001IGFHW6"

    def test_falls_back_to_anchor_when_no_json(self):
        html = (
            '<a class="contributor" href="/Brandon-Sanderson/e/B001IGFHW6?x=1">'
            'Brandon Sanderson</a>'
        )
        assert _extract_author_id_from_html(html) == "B001IGFHW6"

    def test_short_form_anchor(self):
        html = '<a href="/-/e/B001IGFHW6">.</a>'
        assert _extract_author_id_from_html(html) == "B001IGFHW6"

    def test_no_match_returns_none(self):
        html = "<html>no author links here</html>"
        assert _extract_author_id_from_html(html) is None

    def test_id_must_be_ten_chars_uppercase_alnum(self):
        """Defensive — Amazon IDs are 10-char uppercase alphanumeric.
        Lower-case or wrong-length URLs should not match."""
        html = '<a href="/Foo/e/abcdefghij">bad</a>'  # lowercase
        assert _extract_author_id_from_html(html) is None
        html = '<a href="/Foo/e/SHORT123">bad</a>'  # 8 chars
        assert _extract_author_id_from_html(html) is None


class TestNormalizeName:
    def test_strips_punctuation_and_whitespace(self):
        assert _normalize_name("J. N. Chaney") == "jnchaney"
        assert _normalize_name("J.N. Chaney") == "jnchaney"
        assert _normalize_name("J N Chaney") == "jnchaney"

    def test_collapses_case(self):
        assert _normalize_name("BRANDON sanderson") == "brandonsanderson"

    def test_handles_apostrophes_and_hyphens(self):
        assert _normalize_name("Mary-Anne O'Brien") == "maryanneobrien"


class TestPickBestAuthorIdFromSearch:
    def test_exact_normalized_match_wins(self):
        html = _search_html(
            ("Brandon-Sanderson", "B001IGFHW6"),
            ("Daniel-Greene", "B0WRONGAAA"),  # also valid id format
        )
        result = _pick_best_author_id_from_search(html, "Brandon Sanderson")
        assert result == "B001IGFHW6"

    def test_punctuation_difference_still_matches(self):
        """User passes 'J.N. Chaney'; search HTML has slug
        'J-N-Chaney' (Amazon's slug-decode). Normalize collapses
        both to 'jnchaney' → exact match."""
        html = _search_html(("J-N-Chaney", "B009ABCDEF"))
        result = _pick_best_author_id_from_search(html, "J.N. Chaney")
        assert result == "B009ABCDEF"

    def test_no_exact_match_falls_back_to_most_frequent(self, caplog):
        """When no slug normalizes to the queried name, return the
        most-frequently-occurring ID and WARN about imprecision."""
        # ID-A appears on 2 cards (4 anchors), ID-B on 1 (2 anchors).
        html = _search_html(
            ("Some-Author", "BFREQUENT1"),  # 2 anchors
            ("Some-Author", "BFREQUENT1"),  # 2 more (same id)
            ("Other-Author", "BRAREXXXXX"),  # 2 anchors
        )
        with caplog.at_level("WARNING"):
            result = _pick_best_author_id_from_search(html, "Nobody Matches")
        assert result == "BFREQUENT1"
        assert any(
            "no exact-name match" in record.message for record in caplog.records
        )

    def test_returns_none_on_no_anchors(self):
        html = _fat_body("<html>no author anchors anywhere</html>")
        result = _pick_best_author_id_from_search(html, "Brandon Sanderson")
        assert result is None


# ─── Async orchestration tests ──────────────────────────────────


class TestResolveAmazonAuthorId:
    async def test_tier1_success_short_circuits_tier2(self):
        """When known_book_asin is provided and Tier 1 succeeds, we
        should NEVER fire the Tier 2 search GET — that's the whole
        point of the cheap tier."""
        session = MockSession({
            "/dp/B002GYI9C4": MockResponse(
                200, _dp_html_with_contributor_path("B001IGFHW6"),
            ),
            "/s?": MockResponse(200, _search_html(("X-Y", "BWRONGCODE"))),
        })
        result = await resolve_amazon_author_id(
            "Brandon Sanderson",
            known_book_asin="B002GYI9C4",
            session=session,
        )
        assert result == "B001IGFHW6"
        assert any("/dp/B002GYI9C4" in c for c in session.calls)
        assert not any("/s?" in c for c in session.calls), (
            "Tier 2 search must not fire after Tier 1 success"
        )

    async def test_tier1_failure_falls_through_to_tier2(self):
        """Tier 1 detail page returns 404 → fall through to search."""
        session = MockSession({
            "/dp/B002GYI9C4": MockResponse(404, ""),
            "/s?": MockResponse(
                200, _search_html(("Brandon-Sanderson", "B001IGFHW6")),
            ),
        })
        result = await resolve_amazon_author_id(
            "Brandon Sanderson",
            known_book_asin="B002GYI9C4",
            session=session,
        )
        assert result == "B001IGFHW6"
        assert any("/dp/" in c for c in session.calls)
        assert any("/s?" in c for c in session.calls)

    async def test_no_book_asin_skips_to_tier2(self):
        """No known_book_asin → Tier 1 is skipped entirely (no /dp
        GET fired) and we go straight to search."""
        session = MockSession({
            "/s?": MockResponse(
                200, _search_html(("Brandon-Sanderson", "B001IGFHW6")),
            ),
        })
        result = await resolve_amazon_author_id(
            "Brandon Sanderson", session=session,
        )
        assert result == "B001IGFHW6"
        assert not any("/dp/" in c for c in session.calls)

    async def test_both_tiers_fail_returns_none(self):
        session = MockSession({
            "/dp/": MockResponse(404, ""),
            "/s?": MockResponse(200, _fat_body("<html>no anchors</html>")),
        })
        result = await resolve_amazon_author_id(
            "Unknown Author", known_book_asin="B099XXXXXX", session=session,
        )
        assert result is None

    async def test_empty_name_returns_none_no_requests(self):
        session = MockSession()
        result = await resolve_amazon_author_id("", session=session)
        assert result is None
        assert session.calls == []

    async def test_whitespace_only_name_returns_none(self):
        session = MockSession()
        result = await resolve_amazon_author_id("   ", session=session)
        assert result is None

    async def test_tier1_thin_body_treated_as_failure(self):
        """A 200 OK with body <50 KB is the Akamai thin-body soft-
        block signature. Should fall through to Tier 2 rather than
        try to extract from a block-page."""
        session = MockSession({
            "/dp/": MockResponse(200, "<html>thin body</html>"),
            "/s?": MockResponse(
                200, _search_html(("Brandon-Sanderson", "B001IGFHW6")),
            ),
        })
        result = await resolve_amazon_author_id(
            "Brandon Sanderson",
            known_book_asin="B002GYI9C4",
            session=session,
        )
        assert result == "B001IGFHW6"
        # Must have fired both tiers.
        assert any("/dp/" in c for c in session.calls)
        assert any("/s?" in c for c in session.calls)

    async def test_session_close_called_when_owned(self):
        """When the resolver builds its own session (no `session=`
        passed), it must close that session before returning to
        avoid socket leaks. When the caller passes one, close stays
        the caller's responsibility."""
        # We can't trigger the no-session path without curl_cffi
        # installed; smoke-test the inverse — passed session is NOT
        # closed by the resolver.
        session = MockSession({
            "/s?": MockResponse(
                200, _search_html(("Brandon-Sanderson", "B001IGFHW6")),
            ),
        })
        await resolve_amazon_author_id("Brandon Sanderson", session=session)
        assert session.closed is False

    async def test_network_exception_in_tier1_falls_through(self):
        """An exception during the Tier 1 GET (TLS, DNS, timeout)
        should be caught and fall through to Tier 2, not bubble."""

        class FlakySession(MockSession):
            async def get(self, url: str, timeout: float = 15.0):
                if "/dp/" in url:
                    raise ConnectionError("network busted")
                return await super().get(url, timeout=timeout)

        session = FlakySession({
            "/s?": MockResponse(
                200, _search_html(("Brandon-Sanderson", "B001IGFHW6")),
            ),
        })
        result = await resolve_amazon_author_id(
            "Brandon Sanderson",
            known_book_asin="B002GYI9C4",
            session=session,
        )
        assert result == "B001IGFHW6"

    async def test_tier1_logs_method_used_when_resolved(self, caplog):
        session = MockSession({
            "/dp/B002GYI9C4": MockResponse(
                200, _dp_html_with_contributor_path("B001IGFHW6"),
            ),
        })
        with caplog.at_level("INFO"):
            await resolve_amazon_author_id(
                "Brandon Sanderson",
                known_book_asin="B002GYI9C4",
                session=session,
            )
        assert any(
            "tier-1" in record.message for record in caplog.records
        )

    async def test_tier2_logs_method_used_when_resolved(self, caplog):
        session = MockSession({
            "/s?": MockResponse(
                200, _search_html(("Brandon-Sanderson", "B001IGFHW6")),
            ),
        })
        with caplog.at_level("INFO"):
            await resolve_amazon_author_id(
                "Brandon Sanderson", session=session,
            )
        assert any(
            "tier-2" in record.message for record in caplog.records
        )


# ─── Tier 2a: vanity URL ────────────────────────────────────────


class TestVanityUrlTier:
    """`/author/{normalized_name}` 301-redirects to
    `/stores/{Display-Name}/author/{id}`. We harvest the ID from the
    redirect target URL — no body-parsing required. This is the
    rescue path for Kindle-only indie authors whose /s?i=stripbooks
    search returns no anchors at all (e.g. William D. Arand)."""

    async def test_vanity_url_redirect_target_yields_id(self):
        """The mock response carries a `url` attribute pointing at
        the post-redirect URL. The resolver should extract the
        author_id from that URL via the `/stores/.../author/{id}`
        pattern."""
        session = MockSession({
            "/author/williamdarand": MockResponse(
                status_code=200,
                text=_fat_body("<html>arand store page body</html>"),
                url="https://www.amazon.com/stores/William-D.-Arand/author/B01AY7PSG4",
            ),
        })
        result = await resolve_amazon_author_id(
            "William D. Arand", session=session,
        )
        assert result == "B01AY7PSG4"
        # /s search must not have fired — tier-2a short-circuits.
        assert not any("/s?" in c for c in session.calls)

    async def test_vanity_url_404_falls_through_to_search(self):
        """Amazon's vanity index doesn't include every author. A 404
        means "no slug match" — fall through to the search tier."""
        session = MockSession({
            "/author/": MockResponse(404, ""),
            "/s?": MockResponse(
                200, _search_html(("Brandon-Sanderson", "B001IGFHW6")),
            ),
        })
        result = await resolve_amazon_author_id(
            "Brandon Sanderson", session=session,
        )
        assert result == "B001IGFHW6"
        # Both tiers fired.
        assert any("/author/" in c for c in session.calls)
        assert any("/s?" in c for c in session.calls)

    async def test_vanity_url_normalization_matches_resolver(self):
        """The slug sent to Amazon is the normalize_name output —
        lowercase, punctuation stripped, whitespace collapsed. "J.
        N. Chaney" should hit `/author/jnchaney`."""
        session = MockSession({
            "/author/jnchaney": MockResponse(
                status_code=200,
                text=_fat_body("<html>chaney</html>"),
                url="https://www.amazon.com/stores/J.-N.-Chaney/author/B07XYZABCD",
            ),
        })
        result = await resolve_amazon_author_id(
            "J. N. Chaney", session=session,
        )
        assert result == "B07XYZABCD"

    async def test_vanity_url_body_fallback(self):
        """If the response URL didn't redirect (e.g. test setup or
        future Amazon change), the resolver still scans the response
        body for the same `/stores/.../author/{id}` pattern as a
        belt-and-suspenders fallback."""
        body = _fat_body(
            '<html>canonical link <a href="/stores/Foo/author/B0BODYONLY">'
            'Foo</a></html>'
        )
        session = MockSession({
            "/author/foo": MockResponse(
                status_code=200, text=body, url="https://www.amazon.com/author/foo",
            ),
        })
        result = await resolve_amazon_author_id(
            "Foo", session=session,
        )
        assert result == "B0BODYONLY"

    async def test_empty_slug_skips_vanity_lookup(self):
        """A name that normalizes to empty (e.g. just punctuation)
        shouldn't fire the vanity GET — there's no slug to send."""
        session = MockSession()  # no routes
        result = await resolve_amazon_author_id("???", session=session)
        # No vanity hit, no search hit → None.
        assert result is None
        # Critically: no /author/ GET fired (an empty slug would build
        # a malformed URL).
        assert not any("/author/" in c for c in session.calls)


# ─── Tier 2b: multi-variant /s search ───────────────────────────


class TestMultiVariantSearch:
    """The /s search tries `i=digital-text`, then unfiltered, then
    `i=stripbooks` — first non-empty parse wins. Rescues Kindle-only
    indies whose print-store search returns nothing."""

    async def test_digital_text_variant_tried_first(self):
        """If `i=digital-text` returns anchors, the resolver returns
        without firing the other two variants. The Kindle store has
        the best coverage for the indie-author population we care
        about most."""
        session = MockSession({
            "i=digital-text": MockResponse(
                200, _search_html(("William-D-Arand", "B01AY7PSG4")),
            ),
            # If the resolver wrongly fell through to these, it'd
            # pick BWRONGCODE — assertion below catches that.
            "i=stripbooks": MockResponse(
                200, _search_html(("X", "BWRONGCODE")),
            ),
        })
        result = await resolve_amazon_author_id(
            "William D. Arand", session=session,
        )
        assert result == "B01AY7PSG4"
        # First search call should be the digital-text variant.
        search_calls = [c for c in session.calls if "/s?" in c]
        assert search_calls, "expected at least one /s call"
        assert "i=digital-text" in search_calls[0]

    async def test_empty_first_variant_falls_to_unfiltered(self):
        """When `i=digital-text` parses zero anchors, the resolver
        tries the unfiltered variant next."""
        empty_html = _fat_body("<html>no anchors anywhere</html>")
        session = MockSession({
            "i=digital-text": MockResponse(200, empty_html),
            # The second variant has no `i=` param so we route by
            # the lack of `&i=`. Match `/s?k=` exactly.
        })
        # Add the unfiltered route via a second pattern — order
        # matters here because the routes are checked in insertion
        # order. Put the more specific pattern first.
        session.routes["i=stripbooks"] = MockResponse(200, empty_html)
        session.routes["/s?k="] = MockResponse(
            200, _search_html(("Author-Name", "B0FALLBACK")),
        )
        result = await resolve_amazon_author_id(
            "Author Name", session=session,
        )
        assert result == "B0FALLBACK"
        # All three variants tried? No — should stop after the
        # second hit. The first (digital-text) parses 0, the second
        # (unfiltered) parses 1 → success.
        search_calls = [c for c in session.calls if "/s?" in c]
        assert len(search_calls) >= 2

    async def test_all_variants_empty_returns_none(self):
        """When every /s variant parses zero anchors, the resolver
        returns None (caller logs + skips)."""
        empty = _fat_body("<html>nothing</html>")
        session = MockSession({"/s?": MockResponse(200, empty)})
        result = await resolve_amazon_author_id(
            "Mystery Author", session=session,
        )
        assert result is None
        # Confirms all 3 variants were tried.
        search_calls = [c for c in session.calls if "/s?" in c]
        assert len(search_calls) == 3
