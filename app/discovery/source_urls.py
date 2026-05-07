"""
Source URL parser + canonicalizer.

Used by the v2.3.2 source URL editor in the book sidebar: the user
pastes any source URL, we figure out which source it belongs to and
normalize it to the canonical form Seshat stores in
`books.source_url`'s JSON dict.

Why canonicalize rather than just store as-pasted?
  * Goodreads URLs include the title slug — `…/book/show/12345-some-book`
    — which changes if the slug ever shifts. The book id alone is the
    stable identifier; the slug is decoration.
  * Amazon URLs vary wildly (regional domains, redirector wrappers,
    `/dp/<ASIN>` vs `/gp/product/<ASIN>`). The ASIN is the stable
    identifier.
  * Kobo URLs include locale prefixes (`/us/en/ebook/...`) that
    differ per country but resolve to the same book.
  * Stored canonicalization means Seshat's badges + URL backfill all
    point at predictable URL shapes regardless of how the user
    discovered the link.

Public API:
    parse_url(url: str) -> tuple[str, str] | None
        Returns (source_name, canonical_url) for the first matching
        source, or None if no source recognizes the URL.

Source order matters here only for cosmetic reasons — domains don't
overlap so each URL matches at most one entry. The order mirrors the
default ebook priority list for consistency.
"""
from __future__ import annotations

import re
from typing import Optional


# Each entry: (source_name, compiled_regex, canonicalizer)
# canonicalizer takes the regex match's groups and returns the
# canonical URL string. Anchored on full URL via the regex `^…$`
# bounds so a URL has to match the source pattern exactly.
_SOURCE_PATTERNS: list[tuple[str, re.Pattern, callable]] = [
    # Goodreads: /book/show/<id>(-slug)?  Strip the slug.
    (
        "goodreads",
        re.compile(
            r"^https?://(?:www\.)?goodreads\.com/book/show/(\d+)"
            r"(?:[-_/][^?#]*)?(?:[?#].*)?$",
            re.IGNORECASE,
        ),
        lambda m: f"https://www.goodreads.com/book/show/{m.group(1)}",
    ),
    # Hardcover: /books/<slug>. Slug IS the canonical identifier
    # (Hardcover doesn't expose a numeric book id in URLs).
    (
        "hardcover",
        re.compile(
            r"^https?://(?:www\.)?hardcover\.app/books/([^/?#]+)"
            r"(?:[?#].*)?$",
            re.IGNORECASE,
        ),
        lambda m: f"https://hardcover.app/books/{m.group(1).lower()}",
    ),
    # Kobo: /<country>/<lang>/ebook/<slug> or /ebook/<slug> or
    # /<country>/<lang>/audiobook/<slug>. Canonical form is
    # /us/en/{ebook,audiobook}/<slug>.
    (
        "kobo",
        re.compile(
            r"^https?://(?:www\.)?kobo\.com"
            r"(?:/[a-z]{2}/[a-z]{2})?"
            r"/(ebook|audiobook)/([^/?#]+)"
            r"(?:[?#].*)?$",
            re.IGNORECASE,
        ),
        lambda m: (
            f"https://www.kobo.com/us/en/{m.group(1).lower()}/"
            f"{m.group(2)}"
        ),
    ),
    # Amazon: any regional domain, /dp/<ASIN> or /gp/product/<ASIN>,
    # optionally preceded by a slug segment. ASIN is 10 alphanumerics
    # (almost always uppercase; lowercase tolerated for lazy paste).
    (
        "amazon",
        re.compile(
            r"^https?://(?:www\.|smile\.)?amazon\.[a-z.]+"
            r"/(?:[^/]+/)*(?:dp|gp/product)/([A-Z0-9]{10})"
            r"(?:[/?#].*)?$",
            re.IGNORECASE,
        ),
        lambda m: f"https://www.amazon.com/dp/{m.group(1).upper()}",
    ),
    # Audible: regional domains, /pd/<slug>/<asin> or /pd/<asin>.
    # Some old links omit /pd/ prefix entirely — handled by the
    # alternation below.
    (
        "audible",
        re.compile(
            r"^https?://(?:www\.)?audible\.[a-z.]+"
            r"/(?:pd/)?(?:[^/]+/)*([A-Z0-9]{10})"
            r"(?:[/?#].*)?$",
            re.IGNORECASE,
        ),
        lambda m: f"https://www.audible.com/pd/{m.group(1).upper()}",
    ),
    # IBDB (iblist.com): /book.php?id=<n>. Already canonical; just
    # strip extra query params + fragment.
    (
        "ibdb",
        re.compile(
            r"^https?://(?:www\.)?iblist\.com/book\.php\?(?:[^#]*&)?"
            r"id=(\d+)(?:[&#].*)?$",
            re.IGNORECASE,
        ),
        lambda m: f"https://www.iblist.com/book.php?id={m.group(1)}",
    ),
    # Google Books: classic /books?id=<id> or new /books/edition/<slug>/<id>.
    (
        "google_books",
        re.compile(
            r"^https?://books\.google\.com/books\?(?:[^#]*&)?"
            r"id=([A-Za-z0-9_-]+)(?:[&#].*)?$",
            re.IGNORECASE,
        ),
        lambda m: f"https://books.google.com/books?id={m.group(1)}",
    ),
    (
        "google_books",
        re.compile(
            r"^https?://(?:www\.)?google\.com/books/edition/"
            r"(?:[^/]*)/([A-Za-z0-9_-]+)(?:[/?#].*)?$",
            re.IGNORECASE,
        ),
        lambda m: f"https://books.google.com/books?id={m.group(1)}",
    ),
]


def parse_url(url: str) -> Optional[tuple[str, str]]:
    """Identify which source a URL belongs to and return the
    canonical form.

    Returns `(source_name, canonical_url)` on the first match, or
    `None` if no source recognizes the URL. The returned source_name
    matches the keys used in `books.source_url` JSON dicts and in
    `metadata_sources` settings.

    Empty or whitespace-only input → None.
    """
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    for name, pat, canon in _SOURCE_PATTERNS:
        m = pat.match(cleaned)
        if m:
            return (name, canon(m))
    return None
