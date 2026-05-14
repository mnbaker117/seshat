# Metadata sources

Seshat enriches every book it touches by querying a chain of external
metadata providers in priority order. This document describes each
source — what it covers, where it fits in the chain, how it fails, and
how to disable or tune it.

The unified Metadata Sources panel (Settings → Sources) is the
authoritative editor for everything below. The legacy per-source flat
keys (`goodreads_enabled`, `rate_goodreads`, etc.) are still synced for
backward compatibility but should not be edited directly.

## At-a-glance source roster

| Source        | Role                              | Auth                | Rate (default) |
|---------------|-----------------------------------|---------------------|----------------|
| MAM           | Owned-data ground truth           | MAM session         | 2.0s           |
| Goodreads     | Authoritative book metadata       | none (v2.13.0 bypass) | **5.0s**       |
| Hardcover     | Rich metadata + Goodreads bridge  | Bearer API key      | 1.0s           |
| Amazon        | Author-Store discovery + enricher | none (curl_cffi)    | 30.0s          |
| Open Library  | Free ISBN-keyed fallback          | none                | 1.0s           |
| Google Books  | Broad metadata                    | API key (optional)  | 1.5s           |
| Audible       | Audiobook primary source          | none                | 0.5s           |
| Kobo          | Ebook storefront metadata         | none                | 3.0s           |
| IBDB          | Indie publisher coverage          | none                | 1.0s           |

MAM is always first and locked. Everything else is reorderable and
disable-able per content type (ebook vs audiobook) and per role
(enrich vs scan).

## Goodreads (v2.13.0 Stage 6)

Goodreads has the most complete catalog of any of these sources but no
public API. Seshat scrapes the public HTML at `/book/show/{id}` and
`/author/list/{id}` — both **robots-allowed** for the `*` user-agent.
The `/search` endpoint is **explicitly disallowed** and Seshat never
hits it.

### The Cloudflare problem

Goodreads sits behind Cloudflare. From server-side Python clients
(`httpx`, `requests`), Cloudflare's bot manager rejects on the TLS
fingerprint (JA3 check) and returns:

- **HTTP 202** with an empty body, **OR**
- **HTTP 200** with an empty body

…before any real content is fetched. This isn't a rate limit, it isn't
a CAPTCHA, and retrying with the same client doesn't help. The wire-
level signature is the same handshake every request makes — what's
needed is a Chrome-shaped handshake.

### How v2.13.0 fixes it (Phase A)

Seshat now routes every Goodreads request through `curl_cffi`, which
drives `libcurl-impersonate` to replicate Chrome 120's TLS handshake
exactly (cipher suite ordering, BoringSSL extensions, ALPN, h2 frame
patterns). Cloudflare reads the connection as a real Chrome desktop,
the JA3 check passes, and the real page comes back at 1MB+ instead of
the thin-body block-page.

All Goodreads-touching code in Seshat now goes through one central
module — `app/metadata/goodreads_session.py` — so the TLS impersonation,
soft-block detection, runtime-state tracking, and rate-limit jitter are
uniform across:

- The discovery source (`app/discovery/sources/goodreads.py`,
  `/author/list/{id}` and `/book/show/{id}` HTML burst surface)
- The paste-URL importer (`app/discovery/routers/import_export.py`)
- The ID resolver chain's auto_complete tier
  (`app/metadata/goodreads_id_resolver.py`)

### Runtime state + the dispatcher skip

On any soft-block response (202 / empty 2xx), the session module flips
a runtime flag — `goodreads_session_state = "soft_blocked"` — visible
in `settings.json`. Both source-iteration loops (per-book enricher,
per-author scan) check this flag and **skip Goodreads entirely** when
set. Without this gate every iteration pays the full
request → soft-block → next-source roundtrip even after the first
soft-block already told us Goodreads is gated.

The flag clears to `active` automatically on the next successful 200
through the session module, or manually via:

- **Settings → Sources → Goodreads → "Run probe"** — one GET to a
  known-good book. Updates the flag based on the result.
- **Settings → Sources → Goodreads → "Run burst (10×)"** — 10 GETs
  against the canonical Phase-A probe pool at the configured rate
  limit. Surfaces density-based 202s that single probes miss.
- **Settings → Sources → Goodreads → "Mark as active"** — manually
  clear the flag without a probe (use after refreshing IP / waiting
  for Cloudflare's bot-score to decay).

### Weekly canary

A scheduled job (Mondays 03:00 local) does one GET to The Hobbit
(`/book/show/5907`) through the production session module. On 202 it
emits a ntfy notification gated on `notify_on_goodreads_canary_failed`
so users who don't open Settings still notice when Goodreads goes
silent.

### Caching

To minimize Goodreads request volume regardless of bypass strategy,
Seshat caches resolver outcomes in `DATA_DIR/seshat_id_cache.db`:

- **Book ID** lookups: 30-day TTL on hits, 1-day on misses
- **Author bibliography** lookups: 7-day TTL on hits, 6-hour on misses

The cache is keyed identifier-first (ISBN > ASIN > normalized
title+author). Misses are cached too so dead-end ISBNs don't re-probe
Goodreads every scan within the miss-TTL window. The cache prunes
expired rows during the weekly canary tick.

### The resolver chain

When the enricher (or any caller) needs a Goodreads book ID for a book
it doesn't already have one for, the ethical resolver runs three tiers
in order. First hit wins; the chain falls through on misses:

1. **Tier 1 — Goodreads `/book/auto_complete?q={isbn_or_asin}`** —
   undocumented JSON endpoint, NOT in the Disallow list. Identifier-
   based, not free-text. Handles most ebook imports since almost every
   epub/azw3 carries ISBN in file metadata.

2. **Tier 2 — Hardcover GraphQL `book_mappings`** (v2.13.0) — when a
   Hardcover API key is configured, one GraphQL roundtrip resolves
   ISBN/ASIN → Hardcover book → `book_mappings` filtered by
   `platform: { name: { _eq: "Goodreads" } }` → external_id. Returns
   the Goodreads ID without ever touching Goodreads. Skipped silently
   when no API key is set.

3. **Tier 3 — Open Library `?bibkeys=ISBN:{isbn}&jscmd=data`** —
   `identifiers.goodreads[0]` for books OL has cross-referenced. Free,
   no key required. Coverage is sparse for recent self-pub indie titles
   but reliable for older / well-cataloged books.

The chain explicitly does NOT fall back to the disallowed `/search`
endpoint, even though some Calibre plugins do. Holding a higher
standard is a deliberate choice.

### Rate limit + when to tune it

Goodreads's default rate is **5.0s + 0–1s jitter**. This is the Phase-A
conservative pace that gives the Chrome120 fingerprint clean headroom
under burst scans. If your burst probe shows zero soft-blocks at 5.0s,
you can dial down to 3.0s for faster scans. If you see soft-blocks
during the burst probe, dial up to 8.0s or higher; the bot manager is
flagging request density.

### What to do when Goodreads goes silent

1. **Run a probe** (Settings → Sources → Goodreads → Run probe). A 200
   means the bypass is working — flag auto-clears.
2. **If still 202**, run a burst probe. If single passes but burst
   fails, raise the rate limit (8s+).
3. **If both fail**, wait 4-12 hours and re-probe. Cloudflare's
   bot-score decays naturally; the Phase-A bypass works for most
   users after a cooldown.
4. **If failures persist for days**, file a GitHub issue. Phase B
   adds an encrypted cookie panel (paste `cf_clearance` + `_session_id2`
   + browser UA from a fresh browser session) — held in reserve for
   when curl_cffi alone stops being enough.

## Hardcover

Modern, ethical alternative to Goodreads. Smaller catalog (especially
in MAM-popular indie/self-pub genres) but **API-first** — no scraping,
high rate limits, rich data including ratings and social signals.

Required: Bearer API key from hardcover.app → Account → API.

Used by:
- The Hardcover metadata source (search by title+author → returns rich
  metadata)
- The v2.13.0 Goodreads ID resolver's Tier 2 (`book_mappings`
  cross-reference)

## Amazon

Author-Store discovery via `/stores/author/{id}/allbooks` + `/juvec`
POSTs (v2.11.0 Stage 5++). Akamai bot-managed; cleared via the same
curl_cffi Chrome120 impersonation pattern that v2.13.0 uses for
Goodreads. Default rate is 30s/author — Amazon's density check trips
fast for sustained scans.

Server-side `authorFilters.format` picks Kindle (default) / paperback /
hardcover / mass_market for ebook scans, or `audible_audiobook` (default)
for audiobook scans. Set via Settings → Sources → Amazon → Format.

## Open Library

Free, no-key, ISBN-keyed. Strongest signal for older or well-cataloged
books. Now both an enrichment source AND a tier in the Goodreads ID
resolver chain.

## Google Books

Optional API key (Google Cloud, Books API enabled). The unkeyed path
works but has a much lower daily quota.

## Audible

Primary audiobook source. Hydrates its catalog hits through Audnexus
internally — narrator, duration, ASIN. Region-aware (`audible_region`
setting, defaults to "us").

## Kobo

Ebook storefront perspective. Parallelized in v2.11.0 with a
configurable `concurrency` (default 4 workers each respecting
`rate_limit`).

## IBDB

Niche but high-quality for indie ebook publishers. Disabled by default;
enable per use case.

## Disabling a source

Settings → Sources → Metadata Sources panel. Each source has four
toggles per row:

- **Ebook Enrich** — query when enriching an ebook grab
- **Ebook Scan** — include in per-author ebook scans
- **Audiobook Enrich** — same, for audiobooks
- **Audiobook Scan** — same, for audiobook scans

Source-level disable is preferred over modifying priority order — a
disabled source contributes zero requests regardless of where it sits
in the chain.
