"""
Google Books metadata source — public REST API.

Free, no API key needed for basic use (~1000 req/day). Clean JSON
responses with volumeInfo containing title, authors, publisher,
publishedDate, description, ISBN identifiers, pageCount, categories,
language, and image links.

Known limitation: match quality is poor for niche/indie titles, and
there's no native series/series_index field. We parse series from the
title when possible and use Google Books primarily as a fallback for
ISBN and publisher when primary sources (MAM, Goodreads, Hardcover) miss.

Cover quality is mediocre (small thumbnails); we upgrade the zoom
parameter in the URL for the best available version.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.metadata.record import MetaRecord
from app.metadata.sources.base import MetaSource
from app.metadata.text_clean import description_to_plain_text

_log = logging.getLogger("seshat.metadata.google_books")

_API = "https://www.googleapis.com/books/v1/volumes"


class GoogleBooksSource(MetaSource):
    name = "google_books"
    default_timeout = 15.0

    def __init__(self, *, api_key: str = "", rate_limit: float = 0.5):
        super().__init__(rate_limit=rate_limit)
        self._api_key = api_key

    async def search_book(
        self, title: str, author: str, **_,
    ) -> Optional[MetaRecord]:
        if not title:
            return None

        # Google Books query syntax: intitle: and inauthor: qualifiers.
        q = f"intitle:{title}"
        if author:
            q += f"+inauthor:{author}"

        params = {"q": q, "maxResults": "5", "printType": "books"}
        if self._api_key:
            params["key"] = self._api_key

        try:
            resp = await self._get(_API, params=params)
        except Exception:
            _log.debug("google_books: search failed")
            return None

        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None

        # Score and pick the best match.
        from app.metadata.scoring import score_match
        best = None
        best_score = 0.0
        for item in items:
            vi = item.get("volumeInfo", {})
            item_title = vi.get("title", "")
            item_authors = vi.get("authors", [])
            score = score_match(
                record_title=item_title,
                record_authors=item_authors,
                search_title=title,
                search_authors=author,
            )
            if score > best_score:
                best = item
                best_score = score

        if best is None or best_score < 0.3:
            return None

        return _volume_to_record(best)


def _volume_to_record(item: dict) -> MetaRecord:
    vi = item.get("volumeInfo", {})

    # ISBN: prefer ISBN_13, fall back to ISBN_10.
    isbn = None
    for ident in vi.get("industryIdentifiers", []):
        if ident.get("type") == "ISBN_13":
            isbn = ident["identifier"]
            break
        if ident.get("type") == "ISBN_10" and not isbn:
            isbn = ident["identifier"]

    # Cover: upgrade thumbnail URL for better quality.
    cover_url = None
    images = vi.get("imageLinks", {})
    for key in ("large", "medium", "small", "thumbnail", "smallThumbnail"):
        if images.get(key):
            cover_url = _upgrade_cover(images[key])
            break

    # Series: Google has no native field; parse from title.
    series_name, series_index = _parse_series_from_title(vi.get("title", ""))

    # Language: Google uses ISO 639-1 codes (en, fr, de).
    lang = vi.get("language")

    return MetaRecord(
        title=vi.get("title", ""),
        authors=vi.get("authors", []),
        series=series_name,
        series_index=series_index,
        description=description_to_plain_text(vi.get("description")),
        isbn=isbn,
        publisher=vi.get("publisher"),
        pub_date=vi.get("publishedDate"),
        page_count=vi.get("pageCount"),
        language=lang,
        tags=vi.get("categories", []),
        cover_url=cover_url,
        source="google_books",
        source_url=vi.get("infoLink") or vi.get("canonicalVolumeLink"),
        external_id=item.get("id", ""),
    )


def _upgrade_cover(url: str) -> str:
    """Upgrade a Google Books thumbnail URL for maximum quality.

    Replace zoom=1 with zoom=0 for the largest available image, and
    strip the `&edge=curl` parameter that adds a fake page-curl
    overlay.
    """
    url = re.sub(r"zoom=\d", "zoom=0", url)
    url = re.sub(r"&edge=curl", "", url)
    # Ensure HTTPS.
    url = url.replace("http://", "https://")
    return url


def _parse_series_from_title(title: str) -> tuple[Optional[str], Optional[float]]:
    """Try to extract series info from a Google Books title.

    Google Books often encodes series as:
      "Mistborn: The Final Empire"
      "The Way of Kings (The Stormlight Archive, #1)"

    Returns (series_name, index) or (None, None).
    """
    # Pattern: "Title (Series Name, #N)"
    m = re.search(r"\((.+?),?\s*#(\d+(?:\.\d+)?)\)\s*$", title)
    if m:
        try:
            return m.group(1).strip(), float(m.group(2))
        except ValueError:
            return m.group(1).strip(), None
    return None, None


