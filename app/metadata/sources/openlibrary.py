"""
Open Library per-book metadata source.

Companion to `app.discovery.sources.openlibrary` (the per-author
discovery scanner). Where the discovery side walks an author's full
bibliography, this side answers the enricher's single-book question:
"given title + author (and optionally ISBN), what's the best match
on OL and what metadata does it carry?"

Two-tier lookup chain:

  1. **ISBN-keyed** (preferred when ISBN known) — `/api/books?bibkeys=
     ISBN:{isbn}&jscmd=data&format=json`. ISBN is OL's strongest match
     signal; this short-circuits the search ranker entirely.
  2. **Search by title + author** — `/search.json?title=...&author=...`.
     Falls back to scoring the top N hits with the shared `score_match`
     comparator. Same gate (0.3 floor) as Goodreads/Google Books.

Coverage is excellent for older / well-cataloged books and modest for
indie self-pub. v2.11.0 promotes OL into the enricher chain so it can
fill cover / description / publisher / pub_date gaps left by sources
that fail or soft-block on a particular book.

Notes:
  - OL has no first-class series field — best-effort parse from title
    parenthetical, mirroring the Google Books pattern.
  - Author key (`OL...A`) and work key (`OL...W`) are NOT the same;
    `external_id` here is the work key for join-with-discovery.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.metadata.record import MetaRecord
from app.metadata.scoring import score_match
from app.metadata.sources.base import MetaSource

logger = logging.getLogger("seshat.metadata.openlibrary")

_BASE = "https://openlibrary.org"
_COVER_BASE = "https://covers.openlibrary.org/b/id"
_SEARCH_URL = f"{_BASE}/search.json"
_BIBKEYS_URL = f"{_BASE}/api/books"


class OpenLibrarySource(MetaSource):
    name = "openlibrary"
    default_headers = {
        "Accept": "application/json",
        "User-Agent": "Seshat/2.11 (https://github.com/malevolenttortoise/seshat)",
    }
    default_timeout = 20.0

    def __init__(self, *, rate_limit: float = 1.0):
        super().__init__(rate_limit=rate_limit)

    async def search_book(
        self, title: str, author: str, **_,
    ) -> Optional[MetaRecord]:
        """Look up a single book by title + author on Open Library.

        ISBN-keyed lookup is NOT taken in this default signature — the
        enricher dispatcher calls `search_book(title, author)` without
        ISBN context. Callers that already have an ISBN can call
        `search_by_isbn()` directly for the faster path.
        """
        if not title:
            return None

        params: dict[str, Any] = {
            "title": title,
            "limit": 5,
        }
        if author:
            params["author"] = author

        try:
            resp = await self._get(_SEARCH_URL, params=params)
        except Exception:
            logger.debug("openlibrary: search_book failed for %r / %r", title, author)
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        docs = data.get("docs") or []
        if not docs:
            return None

        # Score the top hits and pick the best match. OL's ranker is
        # title-strong, so the first hit is usually right; scoring with
        # the shared comparator guards against the long tail of partial
        # matches.
        best: Optional[dict] = None
        best_score = 0.0
        for d in docs[:5]:
            doc_title = d.get("title") or ""
            doc_authors = d.get("author_name") or []
            score = score_match(
                record_title=doc_title,
                record_authors=doc_authors,
                search_title=title,
                search_authors=author,
            )
            if score > best_score:
                best = d
                best_score = score

        if best is None or best_score < 0.3:
            return None

        return _search_doc_to_record(best)

    async def search_by_isbn(self, isbn: str) -> Optional[MetaRecord]:
        """ISBN-keyed lookup — the fastest, highest-precision path.

        Optional helper for callers that already know the book's ISBN
        (e.g. ingested epub OPF metadata). Bypasses the search ranker
        entirely. Not on the `MetaSource` abstract surface — call
        explicitly when the caller has ISBN context.
        """
        if not isbn:
            return None

        normalized = isbn.replace("-", "").strip()
        params = {
            "bibkeys": f"ISBN:{normalized}",
            "jscmd": "data",
            "format": "json",
        }
        try:
            resp = await self._get(_BIBKEYS_URL, params=params)
        except Exception:
            logger.debug("openlibrary: search_by_isbn failed for %r", isbn)
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        payload = data.get(f"ISBN:{normalized}")
        if not payload:
            return None

        return _bibkeys_to_record(payload, normalized)


# ── Search-result conversion ─────────────────────────────────────────


def _search_doc_to_record(doc: dict) -> MetaRecord:
    """Convert a `/search.json` doc into a MetaRecord."""
    title = (doc.get("title") or "").strip()
    series_name, series_idx = _parse_series_from_title(title)
    cleaned_title = _strip_series_suffix(title) if series_name else title

    # Authors — OL exposes `author_name` (list of strings) in search results
    authors = list(doc.get("author_name") or [])

    # Cover — `cover_i` is OL's cover ID
    cover_url: Optional[str] = None
    cover_id = doc.get("cover_i")
    if isinstance(cover_id, int) and cover_id > 0:
        cover_url = f"{_COVER_BASE}/{cover_id}-L.jpg"

    # Publisher — OL search returns `publisher` as a list
    publishers = doc.get("publisher") or []
    publisher = publishers[0] if publishers else None

    # Pub date — `first_publish_year` is the most consistent signal
    pub_year = doc.get("first_publish_year")
    pub_date = str(pub_year) if isinstance(pub_year, int) else None

    # ISBN — `isbn` is a list of every known ISBN for the work
    isbn_list = doc.get("isbn") or []
    isbn = isbn_list[0] if isbn_list else None

    # Page count — `number_of_pages_median` is OL's best-guess across editions
    page_count = doc.get("number_of_pages_median")
    if not isinstance(page_count, int) or page_count <= 0:
        page_count = None

    # Language — OL returns 3-letter MARC codes (e.g. "eng"); map to 2-letter
    # ISO 639-1 only when we recognize the code. Don't fabricate mappings.
    language = _first_known_language(doc.get("language") or [])

    # Subjects — useful as tags, but limit to first 10 to keep store size sane
    subjects = list(doc.get("subject") or [])[:10]

    # Work key for source URL + external_id
    work_key = (doc.get("key") or "").rsplit("/", 1)[-1]
    source_url = f"{_BASE}/works/{work_key}" if work_key else None

    return MetaRecord(
        title=cleaned_title,
        authors=authors,
        series=series_name,
        series_index=series_idx,
        description=None,  # /search.json doesn't include description
        isbn=isbn,
        publisher=publisher,
        pub_date=pub_date,
        page_count=page_count,
        language=language,
        tags=subjects,
        cover_url=cover_url,
        source="openlibrary",
        source_url=source_url,
        external_id=work_key or None,
    )


# ── ISBN-keyed conversion ─────────────────────────────────────────────


def _bibkeys_to_record(payload: dict, isbn: str) -> MetaRecord:
    """Convert a `/api/books?jscmd=data` payload into a MetaRecord.

    Shape differs significantly from `/search.json` — `jscmd=data` is
    a "rich" view with full edition + work metadata joined together.
    """
    title = (payload.get("title") or "").strip()
    series_name, series_idx = _parse_series_from_title(title)
    cleaned_title = _strip_series_suffix(title) if series_name else title

    # Authors — list of {name, url} dicts
    authors_raw = payload.get("authors") or []
    authors = [a.get("name", "") for a in authors_raw if isinstance(a, dict) and a.get("name")]

    # Cover — `cover` is a dict of size→url; prefer "large"
    cover_url: Optional[str] = None
    covers = payload.get("cover") or {}
    if isinstance(covers, dict):
        cover_url = covers.get("large") or covers.get("medium") or covers.get("small")

    # Publisher — list of {name} dicts; take the first
    pubs_raw = payload.get("publishers") or []
    publisher: Optional[str] = None
    for p in pubs_raw:
        if isinstance(p, dict) and p.get("name"):
            publisher = p["name"]
            break

    pub_date = payload.get("publish_date") or None
    page_count = payload.get("number_of_pages")
    if not isinstance(page_count, int) or page_count <= 0:
        page_count = None

    # Description — sometimes at edition level, sometimes work level. The
    # `/api/books?jscmd=data` view doesn't include description directly;
    # it surfaces an `excerpts` list with text snippets. Use the first.
    description: Optional[str] = None
    excerpts = payload.get("excerpts") or []
    for ex in excerpts:
        if isinstance(ex, dict) and isinstance(ex.get("text"), str):
            description = ex["text"].strip() or None
            if description:
                break

    # Subjects — `subjects` is a list of {name, url}
    subjects_raw = payload.get("subjects") or []
    subjects = [
        s.get("name", "") for s in subjects_raw[:10]
        if isinstance(s, dict) and s.get("name")
    ]

    # Work URL for join-with-discovery — `url` is the canonical edition URL
    source_url = payload.get("url") or f"{_BASE}/isbn/{isbn}"

    return MetaRecord(
        title=cleaned_title,
        authors=authors,
        series=series_name,
        series_index=series_idx,
        description=description,
        isbn=isbn,
        publisher=publisher,
        pub_date=pub_date,
        page_count=page_count,
        language=None,  # bibkeys view doesn't carry MARC language reliably
        tags=subjects,
        cover_url=cover_url,
        source="openlibrary",
        source_url=source_url,
        external_id=isbn,  # ISBN is the join key when this path was taken
    )


# ── Helpers ────────────────────────────────────────────────────────────


_SERIES_RX = re.compile(
    r"\s*\((?P<name>[^,()#]+?)(?:,?\s*#?(?P<num>\d+(?:\.\d+)?))?\)\s*$"
)


def _parse_series_from_title(title: str) -> tuple[Optional[str], Optional[float]]:
    """Pull a trailing "(Series, #N)" suffix off a title.

    Mirrors the discovery-side extractor in
    `app.discovery.sources.openlibrary`. Best-effort; misses are silent.
    """
    if not title:
        return None, None
    m = _SERIES_RX.search(title)
    if not m:
        return None, None
    name = (m.group("name") or "").strip()
    num_raw = m.group("num")
    try:
        idx = float(num_raw) if num_raw else None
    except (ValueError, TypeError):
        idx = None
    return name or None, idx


def _strip_series_suffix(title: str) -> str:
    """Remove the trailing series parenthetical that
    `_parse_series_from_title` consumed."""
    return _SERIES_RX.sub("", title).strip() or title


# Common MARC → ISO 639-1 map. Keep small + canonical; unknown codes
# pass through as None so we don't fabricate language labels.
_LANGUAGE_MAP = {
    "eng": "en",
    "spa": "es",
    "fre": "fr", "fra": "fr",
    "ger": "de", "deu": "de",
    "ita": "it",
    "jpn": "ja",
    "rus": "ru",
    "chi": "zh", "zho": "zh",
    "por": "pt",
    "dut": "nl", "nld": "nl",
    "kor": "ko",
    "pol": "pl",
    "swe": "sv",
    "tur": "tr",
    "ara": "ar",
}


def _first_known_language(codes: list) -> Optional[str]:
    """Pick the first language code we can map to ISO 639-1.

    OL's `language` field comes through as `[{"key": "/languages/eng"}, ...]`
    or plain `["eng", ...]` depending on the view. Handle both shapes.
    """
    for entry in codes:
        if isinstance(entry, dict):
            raw = entry.get("key") or entry.get("name") or ""
            raw = raw.rsplit("/", 1)[-1]
        elif isinstance(entry, str):
            raw = entry
        else:
            continue
        mapped = _LANGUAGE_MAP.get(raw.lower())
        if mapped:
            return mapped
    return None
