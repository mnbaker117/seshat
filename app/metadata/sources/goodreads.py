"""
Goodreads metadata source.

Book-centric scraper. Historically two-pass:

  1. `/search?q={title author}&search_type=books` — find a candidate
     book id by title + author.
  2. `/book/show/{book_id}` — fetch the detail page for rich fields.

**v2.10.4 — `/search` calls dropped.** Goodreads' robots.txt
explicitly disallows `/search` for the `*` user-agent. This source
no longer hits it. Free-text `search_book(title, author)` calls
now return None with an informational log; the enricher's
dispatcher just moves to the next source.

The `/book/show/{id}` path (which IS robots-permitted) remains
functional — see `_merge_detail_page()` and the manual paste-URL
import path at `app/discovery/routers/import_export.py`.

v2.11.0 will wire the ethical `goodreads_id_resolver` chain
(`/book/auto_complete` → Hardcover `book_mappings` → Open Library
`identifiers.goodreads`) so this source can recover its enrichment
role for books where we can resolve a goodreads_id from sanctioned
APIs without ever hitting `/search`.

This source also detects Cloudflare's 202-with-empty-body soft-
block and logs distinctly (so future "Goodreads cookies expired"
vs "Goodreads doesn't have this book" diagnostics aren't muddled).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from app.metadata.record import MetaRecord
from app.metadata.sources.base import MetaSource

_log = logging.getLogger("seshat.metadata.goodreads")

_BASE = "https://www.goodreads.com"

# Goodreads will serve the bot page if the User-Agent looks like a
# headless client, so we claim a normal Firefox UA.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

def _is_cloudflare_soft_block(resp) -> bool:
    """Detect Goodreads' Cloudflare 202-with-empty-body interstitial.

    The cps gate returns `HTTP 202 Accepted` with a 0-byte body when
    it wants the client to solve a JS challenge. Browsers do; httpx
    doesn't. Either status 202 OR a 2xx with empty body counts.
    """
    if resp is None:
        return False
    if resp.status_code == 202:
        return True
    if 200 <= resp.status_code < 300 and not (resp.content or b""):
        return True
    return False


class GoodreadsSource(MetaSource):
    name = "goodreads"
    default_headers = _DEFAULT_HEADERS
    default_timeout = 45.0

    async def search_book(
        self, title: str, author: str  # noqa: ARG002 — required by MetaSource interface
    ) -> Optional[MetaRecord]:
        """Free-text title+author search — DISABLED in v2.10.4.

        Goodreads' robots.txt explicitly disallows `/search` for the
        `*` user-agent. This method previously hit that endpoint to
        find a candidate book_id, then enriched via `/book/show`.
        Holding a higher standard than Calibre's kiwidude plugin
        (which scrapes `/search` anyway with a rotated browser UA),
        we now skip cleanly and let the enricher's dispatcher move
        on to the next source in the priority chain.

        The ethical resolver chain at
        `app/metadata/goodreads_id_resolver.py` (Tier 1
        `/book/auto_complete` + Tier 3 Open Library) is available
        for callers that have an ISBN/ASIN, but wiring it into the
        enricher requires extending the source interface to pass
        identifiers, which is v2.11.0 scope.
        """
        if not title:
            return None
        _log.info(
            "goodreads: search_book skipped — /search is robots-disallowed; "
            "v2.11.0 will wire goodreads_id_resolver (auto_complete / "
            "Open Library) to recover this path ethically (title=%r)",
            title,
        )
        return None


# ─── HTML parser for /book/show/{id} (robots-permitted) ────────


def _merge_detail_page(record: MetaRecord, html: str) -> None:
    """Extract rich fields from a `/book/show/{id}` HTML page.

    Mutates `record` in place. Nothing here raises — missing fields
    are just left as-is on the record. JSON-LD is preferred over
    HTML scraping where both are available because it's stable
    across Goodreads redesigns.
    """
    soup = BeautifulSoup(html, "lxml")

    # Capture the caller-supplied description (if any) before any
    # selector writes to `record`. Longest-wins below needs this as
    # one of the candidates so a pre-populated field can survive
    # if it's genuinely longer than anything on this page.
    initial_desc = record.description
    jsonld_desc: Optional[str] = None

    # JSON-LD structured data block.
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue

        if data.get("name") and not record.title:
            record.title = str(data["name"])

        date = data.get("datePublished")
        if date and not record.pub_date:
            record.pub_date = str(date)[:10]

        pages = data.get("numberOfPages")
        if pages and not record.page_count:
            try:
                record.page_count = int(pages)
            except (ValueError, TypeError):
                pass

        lang = data.get("inLanguage")
        if lang and not record.language:
            record.language = str(lang)

        image = data.get("image")
        if image and not record.cover_url:
            record.cover_url = str(image)

        isbn = data.get("isbn")
        if isbn and not record.isbn:
            record.isbn = str(isbn).replace("-", "")

        # JSON-LD `description` is frequently the full back-of-book
        # text for Goodreads-mastered entries. When present it's
        # often LONGER than the og:description meta tag's teaser.
        # Collect here rather than writing directly to
        # `record.description` so the post-loop longest-wins step
        # can compare across all candidates.
        desc_ld = data.get("description")
        if desc_ld and not jsonld_desc:
            jsonld_desc = _squeeze(str(desc_ld))

        # Goodreads' author list in JSON-LD is a list of Person objects
        # with `name` fields. Fall back to HTML if missing.
        authors_ld = data.get("author")
        if authors_ld and len(record.authors) <= 1:
            names: list[str] = []
            if isinstance(authors_ld, list):
                for a in authors_ld:
                    if isinstance(a, dict) and a.get("name"):
                        names.append(str(a["name"]))
            elif isinstance(authors_ld, dict) and authors_ld.get("name"):
                names.append(str(authors_ld["name"]))
            if names:
                record.authors = names

    # Publication date fallback: Goodreads doesn't always put
    # `datePublished` in JSON-LD, but the HTML publication paragraph
    # ("First published August 31, 2010") is reliable.
    if not record.pub_date:
        pub_el = soup.find("p", {"data-testid": "publicationInfo"})
        if pub_el:
            parsed = _parse_pub_date_text(pub_el.get_text(" ", strip=True))
            if parsed:
                record.pub_date = parsed

    # Description: collect every candidate on the page, pick longest.
    # Goodreads serves the same description through multiple channels
    # depending on page age / A/B variant:
    #   - JSON-LD `description` (captured into `jsonld_desc` above)
    #   - Schema.org microdata `[itemprop="description"]`
    #   - React-era placeholder `[data-testid="description"]`
    #   - `meta[property="og:description"]` teaser
    # These can differ in length — og:description in particular is
    # often a truncated ~250-char teaser while JSON-LD or the
    # microdata span carries the full back-of-book text. Longest
    # wins so we never lock in the shortest variant. Mirrors the
    # longest-wins merge policy at the enricher level.
    desc_candidates: list[str] = []
    if initial_desc:
        desc_candidates.append(initial_desc)
    if jsonld_desc:
        desc_candidates.append(jsonld_desc)
    for selector in (
        '[itemprop="description"]',
        '[data-testid="description"]',
    ):
        for el in soup.select(selector):
            t = _squeeze(el.get_text(" ", strip=True))
            if t:
                desc_candidates.append(t)
    og_desc = soup.select_one("meta[property='og:description']")
    if og_desc and og_desc.get("content"):
        desc_candidates.append(_squeeze(og_desc["content"]))
    if desc_candidates:
        record.description = max(desc_candidates, key=len)

    # Series info: Goodreads uses `h3.Text__title3` with an anchor for
    # the series name. Shape: "Stormlight Archive #1".
    if not record.series:
        series_el = soup.select_one("h3.Text__title3 a") or soup.select_one(
            "a[href*='/series/']"
        )
        if series_el:
            series_text = _squeeze(series_el.get_text(" ", strip=True))
            name, index = _parse_series_string(series_text)
            if name:
                record.series = name
            if index is not None:
                record.series_index = index

    # Publisher: legacy `details` block.
    if not record.publisher:
        pub_el = soup.find(string=re.compile(r"^Publisher", re.I))
        if pub_el and pub_el.parent:
            next_el = pub_el.parent.find_next_sibling()
            if next_el:
                record.publisher = _squeeze(next_el.get_text(" ", strip=True))


# ─── Tiny text helpers ─────────────────────────────────────────────


_WS_RX = re.compile(r"\s+")


def _squeeze(text: str) -> str:
    return _WS_RX.sub(" ", text or "").strip()


# "First published January 1, 2020" → "2020-01-01"
# "Published August 31, 2010" → "2010-08-31"
# "Expected publication April 4, 2026" → "2026-04-04"
import datetime as _dt  # noqa: E402 — module-scoped by choice

_DATE_FORMATS = (
    "%B %d, %Y",
    "%B %d %Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%B %Y",
    "%b %Y",
    "%Y",
)

_PUB_TEXT_RX = re.compile(
    r"(?:first\s+published|published|expected\s+publication)\s+(.+)$",
    re.IGNORECASE,
)


def _parse_pub_date_text(text: str) -> Optional[str]:
    """Parse a Goodreads publicationInfo paragraph into ISO yyyy-mm-dd."""
    if not text:
        return None
    m = _PUB_TEXT_RX.search(text.strip())
    if not m:
        return None
    remainder = re.sub(r"(\d+)(?:st|nd|rd|th)", r"\1", m.group(1).strip())
    for fmt in _DATE_FORMATS:
        try:
            parsed = _dt.datetime.strptime(remainder, fmt)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_series_string(text: str) -> tuple[Optional[str], Optional[float]]:
    """Split 'Stormlight Archive #1' into ('Stormlight Archive', 1.0)."""
    if not text:
        return None, None
    m = re.match(r"(.*?)\s*#\s*(\d+(?:\.\d+)?)", text)
    if m:
        try:
            return m.group(1).strip(), float(m.group(2))
        except ValueError:
            return m.group(1).strip(), None
    return text.strip(), None
