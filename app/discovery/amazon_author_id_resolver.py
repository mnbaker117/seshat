"""
Amazon Author Store ID resolver — maps {author_name} → {author_id}
(v2.11.0 Stage 5++).

The Author Store ID is the stable identifier Amazon assigns to each
author's branded store page. Looks like ``B001IGFHW6`` (Sanderson)
and appears in URLs:

    /stores/author/B001IGFHW6/allbooks
    /Brandon-Sanderson/e/B001IGFHW6
    /-/e/B001IGFHW6
    /marketplaces/ATVPDKIKX0DER/contributors/authors/B001IGFHW6

AmazonAuthorStoreSource (discovery) needs this ID to drive the
``/stores/author/{id}/allbooks`` GET. We cache the result on
``authors.amazon_id`` once resolved.

Two-tier resolution, first hit wins:

Tier 1 — Existing-book pivot
    If we already have any book for this author with ``books.amazon_id``
    set (e.g. URL-paste import set it), GET that book's ``/dp/{asin}``
    detail page and extract the byLine contributor link. Cheap + exact.

Tier 2 — Search fallback
    GET ``/s?k={author_name}&i=stripbooks``. Parse author-byline
    anchors carrying ``/-/e/{id}`` or ``/Author-Slug/e/{id}`` patterns
    out of every book card. Group by ID, pick the one whose slug
    decodes to the closest match to the queried name. Common-name
    collisions (multiple authors named "John Smith") fall back to a
    best-effort first match with a WARNING log.

Both tiers run behind curl_cffi Chrome-120 impersonation, the same
Akamai bypass the rest of AmazonSource uses (shipped Stage 5+).
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

logger = logging.getLogger("seshat.discovery.amazon.author_id_resolver")


# ─── URL endpoints ───────────────────────────────────────────────


_DP_URL_TEMPLATE = "https://www.amazon.com/dp/{asin}"
_SEARCH_URL = "https://www.amazon.com/s"
# Amazon's author vanity-URL: `/author/{normalized_name}` 301-redirects
# to `/stores/{Display-Name}/author/{author_id}` when the normalized
# name matches an indexed author. Works for any author (Kindle-only,
# print, mainstream, indie) — single request, no result-page parsing,
# no disambiguation needed because the answer is in the redirect URL
# itself. Validated 2026-05-13 for B01AY7PSG4 (Arand), B001IGFHW6
# (Sanderson). 404 when no match — falls through to the search tier.
_VANITY_URL_TEMPLATE = "https://www.amazon.com/author/{slug}"
_VANITY_REDIRECT_RE = __import__("re").compile(
    r'/stores/[^/]+/author/(?P<id>[A-Z0-9]{10})'
)


# ─── Author-ID extraction patterns ───────────────────────────────


# Match `/<slug>/e/<id>` or `/-/e/<id>` URL paths. Captures both the
# slug and the ID. The ID is the 10-char Amazon Standard Identifier
# (Author flavour). The slug is "-" for canonical short-form links
# (`/-/e/B001IGFHW6`) and a name-derived slug for the long form
# (`/Brandon-Sanderson/e/B001IGFHW6`). Accepts trailing `?` or `"` or
# whitespace so we don't over-greedy-match.
_AUTHOR_LINK_RE = re.compile(
    r'/(?P<slug>[^/"\s]+)/e/(?P<id>[A-Z0-9]{10})(?:[?"/\s&]|$)'
)

# Match the JSON-embedded author path in byLine.contributor.author:
#     /marketplaces/ATVPDKIKX0DER/contributors/authors/B001IGFHW6
# This is the most authoritative source — appears in the SSR JSON
# payload on /dp/{asin} pages with the productGrid widget loaded.
_CONTRIBUTOR_PATH_RE = re.compile(
    r'/marketplaces/[A-Z0-9]+/contributors/authors/(?P<id>[A-Z0-9]{10})'
)


# ─── Tier 1: existing-book pivot ─────────────────────────────────


async def _tier1_book_pivot(
    asin: str,
    *,
    session: Any,
    timeout: float,
) -> str | None:
    """GET /dp/{asin}, extract the author ID from byLine markup.

    Returns the author ID on success, None on any failure (network
    error, page parse miss, Akamai soft-block). Caller falls through
    to Tier 2.
    """
    url = _DP_URL_TEMPLATE.format(asin=asin)
    try:
        resp = await session.get(url, timeout=timeout)
    except Exception as exc:  # network, TLS, etc. — log + fall through
        logger.debug("tier1: GET %s failed: %s", url, exc)
        return None

    status = getattr(resp, "status_code", None)
    body = getattr(resp, "text", None) or ""
    if status != 200 or not body:
        logger.debug(
            "tier1: GET %s returned status=%s body_len=%d (no extract)",
            url, status, len(body),
        )
        return None
    # Akamai thin-body soft-block guard — real /dp pages are 200KB+
    if len(body) < 50_000:
        logger.warning(
            "tier1: GET %s thin body (%d bytes) — likely Akamai soft-block",
            url, len(body),
        )
        return None

    return _extract_author_id_from_html(body)


def _extract_author_id_from_html(html: str) -> str | None:
    """Try the JSON contributor path first (most authoritative), then
    fall back to anchor URLs (slug/e/id). Returns the first match."""
    m = _CONTRIBUTOR_PATH_RE.search(html)
    if m:
        return m.group("id")
    m = _AUTHOR_LINK_RE.search(html)
    if m:
        return m.group("id")
    return None


# ─── Tier 2: search fallback ─────────────────────────────────────


def _normalize_name(name: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace. Same
    normalization as the metadata sources use for cross-source name
    matching (matches the Kobo `_kobo_author_matches` pattern shipped
    in v2.10.6)."""
    s = name.lower()
    # Strip every char that isn't alphanumeric (drops periods,
    # commas, hyphens, apostrophes — "J. N. Chaney" / "J.N. Chaney"
    # both collapse to "jnchaney").
    return re.sub(r"[^a-z0-9]", "", s)


async def _tier2_vanity_url(
    author_name: str,
    *,
    session: Any,
    timeout: float,
) -> str | None:
    """GET /author/{normalized_name} and harvest the author_id from
    Amazon's 301-redirect target.

    The vanity URL redirects to `/stores/{Display-Name}/author/{id}`
    when Amazon's index has a matching author. Most reliable single-
    request resolution path; works for Kindle-only indies that the
    `/s?k=...&i=stripbooks` search doesn't surface.

    Returns the author ID on success, None on 404 / no redirect /
    no extractable ID. Caller falls through to /s search variants.
    """
    slug = _normalize_name(author_name)
    if not slug:
        return None
    url = _VANITY_URL_TEMPLATE.format(slug=slug)
    try:
        resp = await session.get(url, timeout=timeout, allow_redirects=True)
    except Exception as exc:
        logger.debug("tier2 vanity: GET %s failed: %s", url, exc)
        return None
    status = getattr(resp, "status_code", None)
    if status != 200:
        # 404 expected when the slug isn't indexed; fall through quietly.
        logger.debug("tier2 vanity: %s returned status=%s", url, status)
        return None
    # The final URL (after redirects) is what carries the author ID.
    # curl_cffi exposes it via `resp.url`. Match against the
    # `/stores/.../author/{id}` portion.
    final_url = str(getattr(resp, "url", "") or "")
    m = _VANITY_REDIRECT_RE.search(final_url)
    if m:
        return m.group("id")
    # Belt-and-suspenders: the body may also contain the ID even if
    # the URL didn't redirect cleanly.
    body = getattr(resp, "text", "") or ""
    m = _VANITY_REDIRECT_RE.search(body)
    if m:
        return m.group("id")
    return None


async def _tier2_search(
    author_name: str,
    *,
    session: Any,
    timeout: float,
) -> str | None:
    """GET `/s?k={author}` against multiple category filters, parse
    author-byline anchors out of the first non-empty result, pick
    the most-matching ID.

    Tries in order:
      1. `i=digital-text` (Kindle store) — best for Kindle-only
         indies; the print-store fallback misses them entirely.
      2. unfiltered — broader coverage if Kindle store had no chip.
      3. `i=stripbooks` (print) — last resort.

    Returns the author ID on the first variant that produces an
    anchor match, None if all three are empty.
    """
    variants = [
        {"k": author_name, "i": "digital-text"},
        {"k": author_name},
        {"k": author_name, "i": "stripbooks"},
    ]
    for params in variants:
        url = f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        try:
            resp = await session.get(url, timeout=timeout)
        except Exception as exc:
            logger.debug("tier2 search: GET %s failed: %s", url, exc)
            continue

        status = getattr(resp, "status_code", None)
        body = getattr(resp, "text", None) or ""
        if status != 200 or not body:
            logger.debug(
                "tier2 search: %s returned status=%s body_len=%d (skipping)",
                url, status, len(body),
            )
            continue
        if len(body) < 50_000:
            logger.warning(
                "tier2 search: %s thin body (%d bytes) — likely Akamai "
                "soft-block; trying next variant",
                url, len(body),
            )
            continue

        result = _pick_best_author_id_from_search(body, author_name)
        if result:
            return result
        logger.debug(
            "tier2 search: %s parsed 0 author anchors; trying next variant",
            url,
        )
    return None


def _pick_best_author_id_from_search(
    html: str, queried_name: str,
) -> str | None:
    """Parse all `/{slug}/e/{id}` anchors, group by ID, and pick the
    ID whose canonical slug best matches the queried name.

    Strategy:
      1. Collect all `_AUTHOR_LINK_RE` matches → list of (slug, id).
      2. Group by `id`. Each ID may have several occurrences with
         different slugs (Amazon's HTML inlines both the short and
         long form on the same card).
      3. For each ID, take the *longest* observed slug (the long
         form usually = decoded name; the short form is "-").
      4. Normalize each long-slug and the queried name; pick the
         ID whose normalized slug == normalized queried name.
      5. If no exact match, fall back to the most-frequent ID with
         a WARNING log noting imprecision.
    """
    matches = _AUTHOR_LINK_RE.findall(html)
    if not matches:
        return None

    # _AUTHOR_LINK_RE.findall returns a list of (slug, id) tuples.
    by_id: dict[str, list[str]] = {}
    for slug, author_id in matches:
        by_id.setdefault(author_id, []).append(slug)

    target_norm = _normalize_name(queried_name)

    # Score each ID: prefer exact normalized-name match.
    exact: list[str] = []
    near: list[tuple[int, str]] = []  # (frequency, id) for tie-break
    for author_id, slugs in by_id.items():
        # The long-form slug is the one with hyphens decoded → a name.
        # The short-form is "-" (the `/-/e/{id}` shape). Pick the
        # longest non-"-" slug; if all are "-", keep "-".
        candidate_slugs = [s for s in slugs if s != "-"]
        long_slug = max(candidate_slugs, key=len) if candidate_slugs else "-"
        slug_norm = _normalize_name(long_slug.replace("-", " "))
        if slug_norm == target_norm:
            exact.append(author_id)
        else:
            near.append((len(slugs), author_id))

    if exact:
        if len(exact) > 1:
            logger.info(
                "tier2: multiple IDs for %r normalize-exact; using first %r",
                queried_name, exact[0],
            )
        return exact[0]

    # No exact match — most-frequent fallback with explicit warn.
    if near:
        near.sort(reverse=True)
        chosen = near[0][1]
        logger.warning(
            "tier2: no exact-name match for %r in %d candidate IDs; "
            "falling back to most-frequent %r",
            queried_name, len(by_id), chosen,
        )
        return chosen
    return None


# ─── Session factory (mirrors AmazonSource pattern) ─────────────


def _create_impersonating_session() -> Any | None:
    """Build a curl_cffi AsyncSession with Chrome 120 TLS impersonation.

    Mirrors `app/discovery/sources/amazon.py:_create_impersonating_session`.
    Kept duplicated rather than imported to avoid circular dep — the
    resolver is called *before* AmazonAuthorStoreSource initializes
    in the scan workflow.

    Returns None if curl_cffi isn't installed; resolver falls back to
    returning None (graceful degradation; caller logs + skips).
    """
    try:
        from curl_cffi.requests import AsyncSession
        return AsyncSession(impersonate="chrome120", timeout=15.0)
    except ImportError:
        logger.warning(
            "amazon_author_id_resolver: curl_cffi not installed — cannot "
            "resolve author IDs without it (Akamai blocks plain httpx). "
            "Install via `pip install curl_cffi`."
        )
        return None


# ─── Public entry point ──────────────────────────────────────────


async def resolve_amazon_author_id(
    author_name: str,
    *,
    known_book_asin: str | None = None,
    session: Any | None = None,
    timeout: float = 15.0,
) -> str | None:
    """Resolve an Amazon Author Store ID for ``author_name``.

    Args:
        author_name: The author's name as Seshat knows it (e.g.
            "Brandon Sanderson" or "J. N. Chaney").
        known_book_asin: If we already have any Amazon ASIN for a
            book by this author, pass it here to enable the cheap
            Tier 1 detail-page pivot.
        session: An async HTTP session with a curl_cffi-style
            ``.get(url, timeout=...)`` interface returning an object
            with ``.status_code`` and ``.text`` attributes. If None,
            a default Chrome-120 impersonating session is built.
        timeout: Per-request timeout in seconds (default 15.0).

    Returns:
        The 10-char Amazon Author Store ID (e.g. "B001IGFHW6"), or
        None if both tiers failed (caller should log + skip Amazon
        discovery for this author).
    """
    if not author_name or not author_name.strip():
        return None

    owns_session = False
    if session is None:
        session = _create_impersonating_session()
        owns_session = True
    if session is None:
        # curl_cffi missing → cannot proceed
        return None

    try:
        if known_book_asin:
            result = await _tier1_book_pivot(
                known_book_asin, session=session, timeout=timeout,
            )
            if result:
                logger.info(
                    "resolved amazon_author_id %r for %r via tier-1 "
                    "(book pivot on %s)",
                    result, author_name, known_book_asin,
                )
                return result

        # Tier 2a: vanity URL — one request, redirect target carries
        # the author_id. Works for any author the Amazon index can
        # match by normalized name, including Kindle-only indies that
        # the print-store search misses entirely.
        result = await _tier2_vanity_url(
            author_name, session=session, timeout=timeout,
        )
        if result:
            logger.info(
                "resolved amazon_author_id %r for %r via tier-2a (vanity URL)",
                result, author_name,
            )
            return result

        # Tier 2b: /s search across multiple category filters, parse
        # author anchors. Slower + less reliable than the vanity URL
        # but catches authors whose normalized name doesn't match
        # Amazon's vanity index (e.g. very common names where the
        # vanity slug points to someone else).
        result = await _tier2_search(
            author_name, session=session, timeout=timeout,
        )
        if result:
            logger.info(
                "resolved amazon_author_id %r for %r via tier-2b (search)",
                result, author_name,
            )
            return result

        logger.info(
            "amazon_author_id resolution FAILED for %r "
            "(tier-1 %s, tier-2a vanity URL miss, "
            "tier-2b search returned no anchor matches)",
            author_name,
            "skipped (no known_book_asin)" if not known_book_asin else "miss",
        )
        return None
    finally:
        if owns_session and hasattr(session, "close"):
            try:
                await session.close()
            except Exception:
                pass
