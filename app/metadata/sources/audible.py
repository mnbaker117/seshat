"""
Audible metadata source — catalog search + Audnexus hydration.

The public Audible catalog endpoint at `api.audible{tld}/1.0/catalog/
products` returns a lightweight hit list for a title+author query.
Each hit carries only an ASIN and a title; to get the full metadata
shape every other source returns, we hydrate by piping each ASIN back
through Audnexus's `/books/{asin}` endpoint.

The region `tld` mapping is identical to ABS's own:

    us: .com   ca: .ca     uk: .co.uk  au: .com.au  fr: .fr
    de: .de    jp: .co.jp  it: .it     in: .in      es: .es

Seshat picks a region from the `audible_region` setting (default
"us"). A mismatch between a user's actual Audible-buying region and
this setting doesn't break anything — Audnexus data is regional
enough that wrong-region lookups may just return slightly different
cover art / pub dates / localized description strings.

Search flow mirrors `Audible.search` in ABS (providers/Audible.js):
  1. If the query looks like an ASIN, do a direct Audnexus fetch.
  2. Otherwise hit the Audible catalog, take up to N hits, hydrate
     each via Audnexus, score title+author similarity, return the
     best scoring result.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.metadata.record import MetaRecord
from app.metadata.sources.audnexus import (
    AudnexusSource,
    VALID_REGIONS,
    _normalize_asin,
)
from app.metadata.sources.base import MetaSource

_log = logging.getLogger("seshat.metadata.audible")

REGION_TLDS = {
    "us": ".com", "ca": ".ca", "uk": ".co.uk", "au": ".com.au",
    "fr": ".fr", "de": ".de", "jp": ".co.jp", "it": ".it",
    "in": ".in", "es": ".es",
}

_MAX_CATALOG_HITS = 5


class AudibleSource(MetaSource):
    name = "audible"
    default_timeout = 15.0

    def __init__(self, *, region: str = "us", rate_limit: float = 0.5):
        super().__init__(rate_limit=rate_limit)
        self.region = region if region in VALID_REGIONS else "us"
        # Inner Audnexus source shares the rate-limit discipline but
        # gets its own httpx client lifecycle. Closed via `close()`.
        self._audnexus = AudnexusSource(region=self.region, rate_limit=rate_limit)

    def _catalog_url(self) -> str:
        tld = REGION_TLDS.get(self.region, ".com")
        return f"https://api.audible{tld}/1.0/catalog/products"

    async def search_book(
        self, title: str, author: str
    ) -> Optional[MetaRecord]:
        if not title:
            return None

        # 1. If the user gave us an ASIN-shaped title, treat it as
        #    a direct lookup. (The user might also drop an ASIN in
        #    when editing review-queue metadata manually.)
        asin_direct = _normalize_asin(title)
        if asin_direct:
            return await self._audnexus.fetch_by_asin(asin_direct)

        # 2. Otherwise query the Audible catalog.
        params: dict[str, str] = {
            "num_results": str(_MAX_CATALOG_HITS),
            "products_sort_by": "Relevance",
            "title": title,
        }
        if author:
            params["author"] = author

        try:
            resp = await self._get(self._catalog_url(), params=params)
        except Exception:
            _log.debug("audible: catalog search failed (region=%s)", self.region)
            return None

        data = resp.json() or {}
        products = data.get("products") or []
        if not products:
            return None

        # Hydrate each candidate via Audnexus. The enricher caps total
        # per-book wall clock at ~60s, so we intentionally keep the
        # candidate set small (see _MAX_CATALOG_HITS).
        candidates: list[MetaRecord] = []
        for product in products[:_MAX_CATALOG_HITS]:
            asin = product.get("asin")
            if not asin:
                continue
            record = await self._audnexus.fetch_by_asin(asin)
            if record is not None:
                candidates.append(record)

        if not candidates:
            return None

        # Score each candidate and return the best. We deliberately
        # score here rather than trusting catalog order — Audible's
        # "Relevance" sort frequently puts an abridged/dramatized
        # version ahead of the unabridged canonical edition.
        from app.metadata.scoring import score_match
        best: Optional[MetaRecord] = None
        best_score = 0.0
        for cand in candidates:
            cand_score = score_match(
                record_title=cand.title,
                record_authors=cand.authors,
                search_title=title,
                search_authors=author,
            )
            if cand_score > best_score:
                best = cand
                best_score = cand_score

        if best is None or best_score < 0.3:
            return None
        # Propagate the scored confidence upward so the enricher knows
        # how good this match actually is. `fetch_by_asin` sets 1.0 for
        # direct hits; this overrides with the search-quality score
        # because the ASIN came from a fuzzy catalog match, not an
        # authoritative source.
        best.source = "audible"
        best.confidence = best_score
        return best

    async def close(self) -> None:
        # Close both our own client and the nested Audnexus client.
        await super().close()
        if self._audnexus is not None:
            await self._audnexus.close()
