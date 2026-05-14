"""Unit tests for `_extract_source_slug` (v2.12.0 slug columns).

Pulls the slug out of source-specific URLs. Stored in
`books.hardcover_slug` / `books.kobo_slug` so the frontend
BookSidebar's `slugDerivedUrl` fallback can reconstruct a working
URL when `source_url` JSON is missing.

Goodreads / Amazon / Google Books / IBDB URLs are numeric/UUID-based
so they have ID fallback; they don't need slug extraction. The
helper returns None for them.
"""
from __future__ import annotations

import pytest

from app.discovery.lookup import _extract_source_slug


class TestHardcoverSlug:
    def test_canonical_url(self):
        assert _extract_source_slug(
            "hardcover", "https://hardcover.app/books/the-way-of-kings",
        ) == "the-way-of-kings"

    def test_canonical_url_no_protocol(self):
        # Some legacy entries omit the protocol — should still match.
        assert _extract_source_slug(
            "hardcover", "hardcover.app/books/mistborn-the-final-empire",
        ) == "mistborn-the-final-empire"

    def test_url_with_query_string(self):
        # Trailing query params shouldn't bleed into the slug.
        assert _extract_source_slug(
            "hardcover", "https://hardcover.app/books/elantris?ref=search",
        ) == "elantris"

    def test_url_with_fragment(self):
        assert _extract_source_slug(
            "hardcover", "https://hardcover.app/books/warbreaker#reviews",
        ) == "warbreaker"

    def test_unrelated_url_returns_none(self):
        assert _extract_source_slug(
            "hardcover", "https://www.goodreads.com/book/show/544240",
        ) is None

    def test_missing_url_returns_none(self):
        assert _extract_source_slug("hardcover", None) is None
        assert _extract_source_slug("hardcover", "") is None


class TestKoboSlug:
    def test_canonical_url(self):
        assert _extract_source_slug(
            "kobo", "https://www.kobo.com/us/en/ebook/the-way-of-kings",
        ) == "the-way-of-kings"

    def test_url_with_session_params(self):
        # Kobo URLs in practice carry sid/ssid/cPos query params.
        # Slug stops at the `?` boundary.
        assert _extract_source_slug(
            "kobo",
            "https://www.kobo.com/us/en/ebook/a-brother-s-price"
            "?sid=58fe624f-566b-4ff5-af36-5734ddd60ed3"
            "&ssid=bz2q_V6TR7OWS5QTgqmff"
            "&cPos=4",
        ) == "a-brother-s-price"

    def test_non_us_region(self):
        # Other regions should also extract cleanly.
        assert _extract_source_slug(
            "kobo", "https://www.kobo.com/gb/en/ebook/mistborn",
        ) == "mistborn"

    def test_unrelated_url_returns_none(self):
        assert _extract_source_slug(
            "kobo", "https://www.amazon.com/dp/B002GYI9C4",
        ) is None


class TestNonSlugSources:
    """Sources whose URLs are numeric/UUID-based shouldn't return a
    slug — they have ID-based fallback in the BookSidebar instead.
    """

    @pytest.mark.parametrize("source_name,url", [
        ("goodreads", "https://www.goodreads.com/book/show/544240"),
        ("amazon", "https://www.amazon.com/dp/B002GYI9C4"),
        ("google_books", "https://books.google.com/books?id=ABC123"),
        ("ibdb", "https://ibdb.dev/book/61c0483c-fec6-404c-8bfd-d684885e58b5"),
    ])
    def test_returns_none_for_non_slug_source(self, source_name, url):
        assert _extract_source_slug(source_name, url) is None

    def test_unknown_source_returns_none(self):
        assert _extract_source_slug("not-a-real-source", "https://example.com/x") is None
