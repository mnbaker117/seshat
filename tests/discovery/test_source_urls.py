"""
v2.3.2 source URL parser tests.

Each public source has at least one positive case (URL parses,
canonicalization matches the documented form) and one negative
case (a URL that should NOT match this source).
"""
from __future__ import annotations

import pytest

from app.discovery.source_urls import parse_url


@pytest.mark.parametrize("url,expected", [
    # Goodreads — drops the title slug.
    (
        "https://www.goodreads.com/book/show/246416427-quarks-and-qi",
        ("goodreads", "https://www.goodreads.com/book/show/246416427"),
    ),
    (
        "http://goodreads.com/book/show/123",
        ("goodreads", "https://www.goodreads.com/book/show/123"),
    ),
    (
        "https://www.goodreads.com/book/show/246416427-quarks-and-qi?ref=foo",
        ("goodreads", "https://www.goodreads.com/book/show/246416427"),
    ),
    # Hardcover — slug-based, lowercased.
    (
        "https://hardcover.app/books/the-final-empire",
        ("hardcover", "https://hardcover.app/books/the-final-empire"),
    ),
    (
        "https://hardcover.app/books/THE-FINAL-EMPIRE",
        ("hardcover", "https://hardcover.app/books/the-final-empire"),
    ),
    # Kobo — country/lang prefix dropped, type retained.
    (
        "https://www.kobo.com/us/en/ebook/the-final-empire",
        ("kobo", "https://www.kobo.com/us/en/ebook/the-final-empire"),
    ),
    (
        "https://www.kobo.com/gb/en/ebook/some-title",
        ("kobo", "https://www.kobo.com/us/en/ebook/some-title"),
    ),
    (
        "https://www.kobo.com/audiobook/the-name-of-the-wind",
        ("kobo", "https://www.kobo.com/us/en/audiobook/the-name-of-the-wind"),
    ),
    # Amazon — strip everything, keep /dp/<ASIN>.
    (
        "https://www.amazon.com/dp/B07DBN3X3X",
        ("amazon", "https://www.amazon.com/dp/B07DBN3X3X"),
    ),
    (
        "https://www.amazon.com/Final-Empire-Mistborn-Brandon-Sanderson/dp/0765350386",
        ("amazon", "https://www.amazon.com/dp/0765350386"),
    ),
    (
        "https://amazon.co.uk/gp/product/B07DBN3X3X",
        ("amazon", "https://www.amazon.com/dp/B07DBN3X3X"),
    ),
    (
        "https://smile.amazon.com/dp/B07DBN3X3X/ref=sr_1_1?keywords=foo",
        ("amazon", "https://www.amazon.com/dp/B07DBN3X3X"),
    ),
    # Audible — strip slug, keep ASIN under /pd/.
    (
        "https://www.audible.com/pd/The-Final-Empire-Audiobook/B002UZJ2VW",
        ("audible", "https://www.audible.com/pd/B002UZJ2VW"),
    ),
    (
        "https://audible.co.uk/pd/B002UZJ2VW",
        ("audible", "https://www.audible.com/pd/B002UZJ2VW"),
    ),
    # IBDB — already canonical; query stripped to just ?id=.
    (
        "https://www.iblist.com/book.php?id=12345",
        ("ibdb", "https://www.iblist.com/book.php?id=12345"),
    ),
    (
        "https://iblist.com/book.php?id=12345&from=search",
        ("ibdb", "https://www.iblist.com/book.php?id=12345"),
    ),
    # Google Books — both URL shapes converge on the canonical form.
    (
        "https://books.google.com/books?id=abcDEF123",
        ("google_books", "https://books.google.com/books?id=abcDEF123"),
    ),
    (
        "https://books.google.com/books?id=abcDEF123&hl=en",
        ("google_books", "https://books.google.com/books?id=abcDEF123"),
    ),
    (
        "https://www.google.com/books/edition/_/abcDEF123",
        ("google_books", "https://books.google.com/books?id=abcDEF123"),
    ),
])
def test_parse_url_positive(url, expected):
    assert parse_url(url) == expected


@pytest.mark.parametrize("url", [
    None,
    "",
    "   ",
    "not a url at all",
    "https://example.com/some/page",
    # Goodreads's old "/work/quotes/" path — not a book page.
    "https://www.goodreads.com/work/quotes/12345",
    # Amazon home page, no /dp/.
    "https://www.amazon.com/",
    # Hardcover homepage — no /books/.
    "https://hardcover.app/",
])
def test_parse_url_negative(url):
    assert parse_url(url) is None


def test_whitespace_tolerated():
    """User pastes from a UI that adds leading/trailing whitespace."""
    result = parse_url(
        "  https://www.goodreads.com/book/show/246416427-quarks-and-qi  "
    )
    assert result == (
        "goodreads", "https://www.goodreads.com/book/show/246416427",
    )
