"""
Audnexus metadata source — Audible catalog proxy at api.audnex.us.

Audnex is a community project that scrapes Audible's catalog and exposes
a clean REST API at https://api.audnex.us. ABS uses it as its primary
Audible data source because Audible itself doesn't provide a documented
public metadata endpoint.

Endpoints used here:
  GET /books/{asin}?region={region}   — book metadata (primary)
  GET /authors?name={name}[&region]   — author ASIN search
  GET /authors/{asin}[?region]        — author detail

Rate limit (per the project README): 100 req/min. We run with a
0.2s `rate_limit` floor which caps us well under the ceiling even
if the enricher fans out to a handful of ASINs for a single search.

This source is mostly useful in two ways:

  1. Direct ASIN lookup when the pipeline extracted an ASIN from an
     m4b's `----:com.apple.iTunes:ASIN` atom. `fetch_by_asin()` is
     the entry point — not part of the enricher's `search_book()`
     contract, but reachable by callers that already have the ASIN.
  2. Title/author search by chaining through AudibleSource, which
     uses Audnexus for hydration after Audible catalog returns
     candidate ASINs.

As a standalone `search_book(title, author)` source, Audnexus has
nothing useful — the API has no title/author search, only ASIN and
author-name lookups. `search_book` returns None accordingly; wire
AudibleSource into the enricher priority instead.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.metadata.record import MetaRecord
from app.metadata.sources.base import MetaSource

_log = logging.getLogger("seshat.metadata.audnexus")

_API = "https://api.audnex.us"

# Audnexus accepts these region codes (matches ABS's regionMap).
VALID_REGIONS = {"us", "ca", "uk", "au", "fr", "de", "jp", "it", "in", "es"}


class AudnexusSource(MetaSource):
    name = "audnexus"
    default_timeout = 15.0

    def __init__(self, *, region: str = "us", rate_limit: float = 0.2):
        super().__init__(rate_limit=rate_limit)
        self.region = region if region in VALID_REGIONS else "us"

    async def search_book(
        self, title: str, author: str
    ) -> Optional[MetaRecord]:
        """No title/author search available on Audnexus — see module docstring."""
        return None

    async def fetch_by_asin(self, asin: str) -> Optional[MetaRecord]:
        """GET /books/{asin} — returns a MetaRecord or None.

        Used directly by the pipeline when an ASIN has been extracted
        from an audiobook file's tags. Also used internally by
        AudibleSource to hydrate catalog hits.
        """
        asin = _normalize_asin(asin)
        if not asin:
            return None
        url = f"{_API}/books/{asin}"
        try:
            resp = await self._get(url, params={"region": self.region})
        except Exception:
            _log.debug("audnexus: fetch by asin %s failed", asin)
            return None

        data = resp.json() or {}
        if not data.get("asin"):
            return None
        record = _item_to_record(data, region=self.region)
        # Direct ASIN hit — we're as confident as you can be that this
        # is the book. The enricher won't re-score because confidence
        # is already at the ceiling.
        record.confidence = 1.0
        return record


def _normalize_asin(asin: str) -> str:
    """Strip whitespace + uppercase. Return '' if the shape is wrong.

    Audible ASINs are 10 uppercase alphanumerics starting with B. We
    accept exactly that pattern — weeding out typos + garbage avoids
    a 400 round-trip to the API.
    """
    import re as _re
    if not asin:
        return ""
    candidate = asin.strip().upper()
    if _re.fullmatch(r"B[0-9A-Z]{9}", candidate):
        return candidate
    return ""


def _item_to_record(item: dict, *, region: str = "us") -> MetaRecord:
    """Flatten an Audnexus book payload into a MetaRecord.

    Audnexus response shape (fields we use):
      asin, title, subtitle, authors[{name, asin}], narrators[{name}],
      publisherName, summary, releaseDate, image, genres[{name, type}],
      seriesPrimary{name, position}, seriesSecondary{name, position},
      language, runtimeLengthMin, formatType, isbn, rating

    Mirrors ABS's `Audible.cleanResult` — same field projection so
    our downstream behaves identically to a fresh ABS scan.
    """
    authors = [a.get("name") for a in (item.get("authors") or []) if a.get("name")]
    narrators = [n.get("name") for n in (item.get("narrators") or []) if n.get("name")]

    series_name: Optional[str] = None
    series_index: Optional[float] = None
    primary = item.get("seriesPrimary") or {}
    if primary.get("name"):
        series_name = primary["name"]
        series_index = _parse_series_position(primary.get("position"))

    # Genres and tags are distinguished via the `type` field.
    genres: list[str] = []
    tags: list[str] = []
    for g in item.get("genres") or []:
        name = g.get("name")
        if not name:
            continue
        if g.get("type") == "genre":
            genres.append(name)
        elif g.get("type") == "tag":
            tags.append(name)

    runtime_min = item.get("runtimeLengthMin")
    duration_sec: Optional[float] = None
    if runtime_min is not None:
        try:
            duration_sec = float(runtime_min) * 60.0
        except (TypeError, ValueError):
            duration_sec = None

    pub_year = None
    release_date = item.get("releaseDate")
    if release_date:
        pub_year = release_date.split("-")[0] if "-" in release_date else release_date

    language = item.get("language")
    if isinstance(language, str) and language:
        language = language[:1].upper() + language[1:]

    abridged = None
    format_type = item.get("formatType")
    if format_type is not None:
        abridged = format_type == "abridged"

    asin = item.get("asin") or ""
    source_url = f"https://audible.com/pd/{asin}" if asin else None

    return MetaRecord(
        title=item.get("title") or "",
        authors=authors,
        series=series_name,
        series_index=series_index,
        description=item.get("summary") or None,
        isbn=item.get("isbn") or None,
        publisher=item.get("publisherName") or None,
        pub_date=pub_year,
        page_count=None,  # audiobooks don't have page counts
        language=language,
        tags=genres + tags,
        cover_url=item.get("image") or None,
        source="audnexus",
        source_url=source_url,
        external_id=asin or None,
        confidence=0.0,
        narrator=", ".join(narrators) if narrators else None,
        duration_sec=duration_sec,
        asin=asin or None,
        abridged=abridged,
    )


def _parse_series_position(position) -> Optional[float]:
    """Audnexus position can be '1', '1.5', '2, Dramatized Adaptation', ''.

    Matches ABS's `cleanSeriesSequence`: pull the first numeric run
    (optionally with decimals). Returns None if nothing numeric shows.
    """
    if position is None:
        return None
    if isinstance(position, (int, float)):
        try:
            return float(position)
        except (TypeError, ValueError):
            return None
    import re as _re
    m = _re.search(r"\.\d+|\d+(?:\.\d+)?", str(position))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None
