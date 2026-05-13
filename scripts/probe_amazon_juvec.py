#!/usr/bin/env python3
"""
Diagnostic probe for the Amazon Author-Store ``/juvec`` endpoint.

Runs a live, end-to-end exercise of the Stage 5++ workflow against
one author, capturing the responses so we can verify:

  - The initial allbooks GET returns the expected widget JSON
    behind our curl_cffi Chrome-120 session.
  - Anonymous (no logged-in customerId / customerIP) /juvec POSTs
    actually work — Mark's captured cURLs were from his browser
    session, so this is the open question.
  - The /juvec response framing matches what
    `amazon_widget_parser.parse_juvec_response` expects.
  - Filter-application (`fetch_filtered_page`) and detail-fetch
    (`fetch_asin_batch`) both return populated products.

Usage (inside the Seshat container, where curl_cffi is installed):

    docker exec Seshat python /app/scripts/probe_amazon_juvec.py B001IGFHW6
    # author-id defaults to B001IGFHW6 (Sanderson) if omitted

Output:
  - Stdout: human-readable summary
  - Files in /tmp/seshat-juvec-probe/ (or --out-dir):
      allbooks.html                  — the SSR HTML
      filter_response.json           — the /juvec filter-application response
      detail_response.json           — the /juvec detail-fetch response
      summary.json                   — extracted shape diagnostics

This script does NOT modify any DB state. It only fetches +
writes files. Safe to re-run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Bootstrap the seshat package import path when invoked directly.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.discovery.sources.amazon_widget_parser import (  # noqa: E402
    ParseError,
    parse_allbooks_html,
)
from app.discovery.sources.amazon_juvec_client import (  # noqa: E402
    JuvecClient,
    JuvecError,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("probe-juvec")


ALLBOOKS_URL_TEMPLATE = "https://www.amazon.com/stores/author/{author_id}/allbooks"


def _build_session():
    """Construct a curl_cffi AsyncSession with Chrome 120 TLS
    impersonation, matching AmazonSource's production transport."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        log.error(
            "curl_cffi not installed. Install via `pip install curl_cffi` "
            "or run this script inside the Seshat production container."
        )
        sys.exit(1)
    return AsyncSession(impersonate="chrome120", timeout=30.0)


async def probe(author_id: str, out_dir: Path, format_filter: str, language: str):
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: GET allbooks ─────────────────────────────────────
    url = ALLBOOKS_URL_TEMPLATE.format(author_id=author_id)
    log.info("GET %s", url)
    async with _build_session() as session:
        resp = await session.get(url, timeout=30.0)
        status = resp.status_code
        body = resp.text or ""
        log.info("  status=%d body_len=%d", status, len(body))
        (out_dir / "allbooks.html").write_text(body)

        if status != 200 or len(body) < 50_000:
            log.error(
                "allbooks fetch failed (status=%d, len=%d) — Akamai "
                "soft-block or non-author page. Aborting.",
                status, len(body),
            )
            return 2

        try:
            page_data = parse_allbooks_html(body)
        except ParseError as exc:
            log.error("Parse failed: %s", exc)
            return 3

        log.info(
            "Parsed: author_id=%s, totalResultCount=%d, asin_list=%d, "
            "products=%d, available_languages=%d",
            page_data.author_id,
            page_data.total_result_count,
            len(page_data.asin_list),
            len(page_data.products),
            len(page_data.available_languages),
        )

        # ── Stage 2: POST /juvec filter-application ─────────────
        log.info(
            "POST /juvec (filter-application page=1 format=%r language=%r)",
            format_filter, language,
        )
        client = JuvecClient(page_data, session, burst_delay_s=0.0)
        try:
            filter_resp = await client.fetch_filtered_page(
                page=1, format_filter=format_filter, language=language,
            )
            log.info(
                "  ok: %d products, asin_list=%d, totalResultCount=%s",
                len(filter_resp.products),
                len(filter_resp.asin_list),
                filter_resp.total_result_count,
            )
            (out_dir / "filter_response.json").write_text(
                json.dumps(filter_resp.raw_content, indent=2),
            )
        except JuvecError as exc:
            log.error("filter-application failed: %s", exc)
            filter_resp = None

        # ── Stage 3: POST /juvec detail-fetch ────────────────────
        # Take 16 ASINs from the tail of page 1's ASINList — those
        # are typically the ones not yet populated in the SSR
        # response, so the client would normally request them next.
        tail_asins = list(page_data.asin_list[-16:])
        log.info("POST /juvec (detail-fetch n=%d)", len(tail_asins))
        try:
            detail_resp = await client.fetch_asin_batch(
                tail_asins, format_filter="allFormats", language="All Languages",
            )
            log.info(
                "  ok: %d products returned",
                len(detail_resp.products),
            )
            (out_dir / "detail_response.json").write_text(
                json.dumps(detail_resp.raw_content, indent=2),
            )
        except JuvecError as exc:
            log.error("detail-fetch failed: %s", exc)
            detail_resp = None

        # ── Stage 4: shape diagnostics ──────────────────────────
        summary = {
            "author_id_requested": author_id,
            "author_id_parsed": page_data.author_id,
            "page1_total_result_count": page_data.total_result_count,
            "page1_total_count": page_data.total_count,
            "page1_asin_list_len": len(page_data.asin_list),
            "page1_populated_products": len(page_data.products),
            "available_languages": list(page_data.available_languages),
            "ssr_csrf_tokens_present": {
                "slate_token": bool(page_data.slate_token),
                "fresh_cart_csrf_token": bool(page_data.fresh_cart_csrf_token),
                "amazon_api_csrf_token": bool(page_data.amazon_api_csrf_token),
                "visit_id": bool(page_data.visit_id),
            },
            "filter_application": _summarize_juvec(filter_resp),
            "detail_fetch": _summarize_juvec(detail_resp),
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        log.info("Summary written to %s/summary.json", out_dir)
        return 0


def _summarize_juvec(resp):
    if resp is None:
        return {"status": "FAILED"}
    return {
        "status": "OK",
        "products": len(resp.products),
        "asin_list": len(resp.asin_list),
        "total_result_count": resp.total_result_count,
        "raw_content_top_level_keys": sorted(resp.raw_content.keys()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "author_id", nargs="?", default="B001IGFHW6",
        help="Amazon Author Store ID (default: B001IGFHW6 = Brandon Sanderson)",
    )
    parser.add_argument(
        "--out-dir", default="/tmp/seshat-juvec-probe",
        help="Directory for response artifacts (default: %(default)s)",
    )
    parser.add_argument(
        "--format", default="kindle", dest="format_filter",
        help="Filter format value (default: %(default)s)",
    )
    parser.add_argument(
        "--language", default="English",
        help="Filter language value (default: %(default)s)",
    )
    args = parser.parse_args()

    return asyncio.run(probe(
        args.author_id,
        Path(args.out_dir),
        args.format_filter,
        args.language,
    ))


if __name__ == "__main__":
    sys.exit(main())
