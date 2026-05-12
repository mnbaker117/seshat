"""
Open Library discovery source — sanctioned REST API.

Open Library publishes a free, no-key REST API at openlibrary.org.
Two-phase shape mirrors the v2.10.5 Hardcover pattern:

  1. Author lookup via `/search/authors.json?q={name}`. Returns a
     ranked list of author records with `key`, `name`, and
     `work_count`. Disambiguate by strict name match + work_count
     tiebreaker.
  2. Walk the author's works via `/authors/{key}/works.json`,
     paginated with `limit` + `offset`. One round-trip per page.

Open Library is data-quality dependent — coverage is good for
older / well-cataloged books, sparse for indie self-pub. We
accept everything OL returns and let downstream consensus
handle merge with richer sources (Hardcover, Goodreads-when-
available).

Series information: OL doesn't have a first-class series
concept. We attempt extraction from the work's title pattern
("Mistborn (Mistborn, #1)") and fall back to standalone. Most
series-rich books will get their series filled in by other
sources during the consensus merge.

Editions / ISBN / page count / language: deferred to v2.11.x.
Each work would require an additional `/works/{key}/editions.json`
fetch — N+1 cost would dominate scan time. The work-level
metadata (title, description, cover, first_publish_year) is
enough for discovery; richer sources backfill the rest.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.discovery.sources.base import (
    AuthorResult,
    BaseSource,
    BookResult,
    SeriesResult,
)

logger = logging.getLogger("seshat.discovery.openlibrary")

BASE = "https://openlibrary.org"
COVER_BASE = "https://covers.openlibrary.org/b/id"


# Series-from-title extractor — Open Library titles often carry
# series info in the title field as "(Series Name, #N)" or
# "Series Name, Book N". Best-effort; misses go to standalone.
_SERIES_RX = re.compile(
    r"\s*\((?P<name>[^,()#]+?)(?:,?\s*#?(?P<num>\d+(?:\.\d+)?))?\)\s*$"
)

# Common edition / format decorations that get parenthesized in OL
# titles and would otherwise be misread as a series name. Exact-
# lowercase lookup PLUS a regex catch for "Nth Edition" / "Nth Ed"
# patterns so "2nd Edition", "3rd Ed", "10th Edition" all skip.
_EDITION_REJECT_EXACT = frozenset({
    "edition", "annotated", "illustrated", "unabridged", "abridged",
    "revised", "expanded", "deluxe edition", "anniversary edition",
    "collector's edition", "special edition", "hardcover", "paperback",
    "ebook", "audiobook", "audio", "kindle edition", "large print",
    "boxed set", "box set", "omnibus",
})
_EDITION_REJECT_RX = re.compile(
    r"^\d+(?:st|nd|rd|th)\s+(?:edition|ed\.?|printing)$",
    re.IGNORECASE,
)


def _extract_series_from_title(title: str) -> tuple[Optional[str], Optional[float], str]:
    """Pull a trailing "(Series, #N)" suffix off a title.

    Returns (series_name, series_index, cleaned_title). When no
    pattern matches, returns (None, None, title) unchanged.
    """
    if not title:
        return None, None, title
    m = _SERIES_RX.search(title)
    if not m:
        return None, None, title
    name = m.group("name").strip()
    num_raw = m.group("num")
    try:
        idx = float(num_raw) if num_raw else None
    except (ValueError, TypeError):
        idx = None
    cleaned = title[:m.start()].strip()
    return name or None, idx, cleaned or title


def _norm_for_match(name: str) -> str:
    """Normalize an author name for the strict-match disambiguation gate.
    Lowercases, strips periods/spaces. Same shape as the v2.10.5
    Hardcover resolver."""
    return name.lower().replace(".", "").replace(" ", "")


class OpenLibrarySource(BaseSource):
    name = "openlibrary"
    default_headers = {
        "Accept": "application/json",
        "User-Agent": "Seshat/2.10 (https://github.com/malevolenttortoise/seshat)",
    }
    default_timeout = 30.0

    def __init__(self, rate_limit: float = 1.0):
        super().__init__(rate_limit=rate_limit)

    async def search_author(self, author_name: str) -> Optional[AuthorResult]:
        """Resolve `author_name` to an OL author key + walk their works.

        Mirrors the v2.10.5 Hardcover pattern: phase 1 finds the
        canonical author record (with namesake disambiguation), phase 2
        paginates the author's works relation. Returns a populated
        `AuthorResult` so the downstream `_try_source` fast path skips
        the redundant `get_author_books` call.
        """
        ol_key = await self._resolve_author_key(author_name)
        if not ol_key:
            logger.info(
                "  OpenLibrary: no author match for '%s'", author_name,
            )
            return None

        works = await self._fetch_all_author_works(ol_key)
        if not works:
            logger.info(
                "  OpenLibrary: author %s '%s' returned 0 works",
                ol_key, author_name,
            )
            return AuthorResult(
                name=author_name, external_id=ol_key,
            )

        logger.info(
            "  OpenLibrary: author %s '%s' → %d works",
            ol_key, author_name, len(works),
        )
        return self._build_result(author_name, ol_key, works)

    async def get_author_books(
        self, author_id: str, **_kw,
    ) -> Optional[AuthorResult]:
        """No-op — `search_author` already returns books in one shot.

        Same pattern as HardcoverSource; lookup.py's two-phase flow
        collapses to phase 1. Returning None here degrades cleanly
        on the unreachable slow path.
        """
        return None

    # ── Phase 1 — author lookup ───────────────────────────────────

    async def _resolve_author_key(
        self, author_name: str
    ) -> Optional[str]:
        """Find the OL author key matching `author_name`.

        Strict normalized-name match preferred; ties broken by
        `work_count` (more prolific = more likely the user's target).
        Falls back to OL's top-ranked match when no name passes the
        strict gate (OL's ranker is generally reliable for first hits).
        """
        try:
            resp = await self._get(
                f"{BASE}/search/authors.json",
                params={"q": author_name, "limit": 10},
            )
            data = resp.json()
        except Exception as e:
            logger.debug(
                "  OpenLibrary: author search error for '%s': %s",
                author_name, e,
            )
            return None

        docs = data.get("docs") or []
        if not docs:
            return None

        target = _norm_for_match(author_name)
        scored: list[tuple[int, int, str, str]] = []  # (score, work_count, name, key)
        for d in docs:
            key = d.get("key")
            name = d.get("name") or ""
            work_count = int(d.get("work_count") or 0)
            if not key or not name:
                continue
            normalized = _norm_for_match(name)
            score = 0
            if normalized == target:
                score = 100
            elif target in normalized or normalized in target:
                score = 50
            if score == 0:
                continue
            scored.append((score, work_count, name, key))

        if not scored:
            # No name passed strict gate — trust OL's ranker (top hit).
            top = docs[0]
            top_key = top.get("key")
            if top_key:
                logger.info(
                    "  OpenLibrary: no strict name match for '%s', "
                    "falling back to top hit '%s' (key=%s)",
                    author_name, top.get("name"), top_key,
                )
            return top_key

        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        winner = scored[0]
        if len(scored) > 1:
            others = ", ".join(
                f"{n}({c})" for _, c, n, _ in scored[1:5]
            )
            logger.info(
                "  OpenLibrary: disambiguated '%s' → key=%s name=%r "
                "work_count=%d. Passed-over namesakes: %s",
                author_name, winner[3], winner[2], winner[1], others,
            )
        return winner[3]

    # ── Phase 2 — walk author's works ─────────────────────────────

    async def _fetch_all_author_works(self, author_key: str) -> list[dict]:
        """Page through `/authors/{key}/works.json` until exhausted.

        OL caps per-page at 1000 but defaults to 50. We use 100 to
        match the Hardcover pagination shape and keep response sizes
        sane for very prolific authors. Stops on the partial-page
        signal (last page returned fewer than `limit` items).
        """
        # Author key may come in as `/authors/OL26320A` or `OL26320A`;
        # normalize to the bare id portion for path construction.
        bare = author_key.rsplit("/", 1)[-1]
        PAGE_SIZE = 100
        MAX_PAGES = 30  # 3000-work ceiling — well above any single human
        all_works: list[dict] = []
        offset = 0
        for _ in range(MAX_PAGES):
            try:
                resp = await self._get(
                    f"{BASE}/authors/{bare}/works.json",
                    params={"limit": PAGE_SIZE, "offset": offset},
                )
                data = resp.json()
            except Exception as e:
                logger.debug(
                    "  OpenLibrary: works fetch error at offset=%d: %s",
                    offset, e,
                )
                break
            entries = data.get("entries") or []
            if not entries:
                break
            all_works.extend(entries)
            if len(entries) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return all_works

    # ── Result assembly ────────────────────────────────────────────

    def _build_result(
        self, author_name: str, ol_key: str, works: list[dict],
    ) -> AuthorResult:
        """Convert OL work entries into BookResult/SeriesResult shape."""
        series_map: dict[str, SeriesResult] = {}
        standalone: list[BookResult] = []

        for w in works:
            br = self._work_to_book_result(w)
            if br is None:
                continue
            if br.series_name:
                sm = series_map.setdefault(
                    br.series_name,
                    SeriesResult(name=br.series_name, books=[]),
                )
                sm.books.append(br)
            else:
                standalone.append(br)

        return AuthorResult(
            name=author_name,
            external_id=ol_key,
            books=standalone,
            series=list(series_map.values()),
        )

    def _work_to_book_result(self, work: dict) -> Optional[BookResult]:
        title_raw = (work.get("title") or "").strip()
        if not title_raw:
            return None

        # Series extraction from title — best-effort. Only consume
        # the parenthetical when it looks series-shaped (has a
        # name component); leave format / edition decorations alone.
        series_name, series_idx, cleaned_title = _extract_series_from_title(title_raw)
        if series_name:
            lname = series_name.lower().strip()
            if (
                lname in _EDITION_REJECT_EXACT
                or _EDITION_REJECT_RX.match(lname)
            ):
                series_name, series_idx = None, None
                cleaned_title = title_raw

        # Cover — OL's `covers` field is a list of cover_ids
        cover_url: Optional[str] = None
        covers = work.get("covers")
        if isinstance(covers, list) and covers:
            first = covers[0]
            if isinstance(first, int) and first > 0:
                cover_url = f"{COVER_BASE}/{first}-L.jpg"

        # Description — OL stores it as either a string or a
        # `{type, value}` dict.
        description: Optional[str] = None
        desc = work.get("description")
        if isinstance(desc, dict):
            description = desc.get("value")
        elif isinstance(desc, str):
            description = desc
        if description:
            description = description.strip() or None

        # Pub date — OL exposes `first_publish_date` (string) or
        # falls back to `created.value` (timestamp). Prefer the
        # former; degrade to None.
        pub_date = work.get("first_publish_date")
        if isinstance(pub_date, str):
            pub_date = pub_date.strip()
        elif pub_date is not None:
            pub_date = str(pub_date)

        # Work key for the source URL
        work_key = (work.get("key") or "").rsplit("/", 1)[-1]
        source_url = f"{BASE}/works/{work_key}" if work_key else None

        return BookResult(
            title=cleaned_title,
            series_name=series_name,
            series_index=series_idx,
            cover_url=cover_url,
            description=description,
            pub_date=pub_date,
            external_id=work_key or None,
            source=self.name,
            source_url=source_url,
        )
