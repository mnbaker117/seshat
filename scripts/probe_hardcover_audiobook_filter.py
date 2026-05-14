#!/usr/bin/env python3
"""
Diagnostic probe for the Hardcover audiobook-filter approach (v2.12.0 Phase 1.0).

UAT 2026-05-14 surfaced that audiobook scans return books with NO
audiobook editions (the contributions relation isn't filtered; only
the editions sub-array is filtered, so print-only books still come
back with `editions: []`). Two candidate fixes:

  (GraphQL) Filter contributions at the API by requiring
            at least one edition with the target format:
              contributions(where: {
                book: {editions: {reading_format_id: {_in: $fmt_ids}}}
              })

  (Client)  Keep the existing query, drop books with empty editions
            arrays after fetch.

This probe runs BOTH against the same author with the same params,
and compares result quality:
  - total books returned by each variant
  - of the current-shape results, how many have empty editions
    (the leak we want to plug)
  - whether the GraphQL variant loses any books that DO have a
    matching edition (would indicate false-negative)

Author defaults to "Brandon Sanderson" (Hardcover ID 204214). Override
with --name or --author-id.

Usage (inside the Seshat container):

    docker exec -e PYTHONPATH=/app Seshat python /tmp/probe_hardcover_audiobook_filter.py

Output:
  - Stdout summary
  - JSON dump at /tmp/seshat-hardcover-probe/result.json

Safe to re-run — no DB writes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx

from app.config import load_settings


HARDCOVER_GQL_URL = "https://api.hardcover.app/v1/graphql"

# Current production query (unfiltered contributions, editions filtered).
QUERY_CURRENT = """
query AuthorBooks($id: Int!, $limit: Int!, $offset: Int!, $format_ids: [Int!]) {
  authors(where: {id: {_eq: $id}}) {
    id name books_count
    contributions(
      limit: $limit
      offset: $offset
      order_by: {book: {release_date: asc_nulls_last}}
    ) {
      book {
        id title release_date
        editions(
          where: {reading_format_id: {_in: $format_ids}}
          order_by: {users_count: desc_nulls_last}
          limit: 1
        ) { id reading_format_id title }
      }
    }
  }
}
"""

# Proposed GraphQL-side filter (contributions where book has a
# matching edition).
QUERY_GRAPHQL_FILTER = """
query AuthorBooks($id: Int!, $limit: Int!, $offset: Int!, $format_ids: [Int!]) {
  authors(where: {id: {_eq: $id}}) {
    id name books_count
    contributions(
      where: {book: {editions: {reading_format_id: {_in: $format_ids}}}}
      limit: $limit
      offset: $offset
      order_by: {book: {release_date: asc_nulls_last}}
    ) {
      book {
        id title release_date
        editions(
          where: {reading_format_id: {_in: $format_ids}}
          order_by: {users_count: desc_nulls_last}
          limit: 1
        ) { id reading_format_id title }
      }
    }
  }
}
"""


async def _fetch(client: httpx.AsyncClient, key: str, query: str, vars: dict) -> dict:
    # Match HardcoverSource._get_client logic: only add `Bearer ` prefix
    # when the stored token doesn't already include a space (some users
    # paste the raw token, others paste "Bearer xxx").
    auth = key if " " in key else f"Bearer {key}"
    r = await client.post(
        HARDCOVER_GQL_URL,
        headers={"Authorization": auth, "Content-Type": "application/json"},
        json={"query": query, "variables": vars},
        timeout=30.0,
    )
    r.raise_for_status()
    body = r.json()
    if "errors" in body:
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    return body


def _summarize(label: str, books: list[dict]) -> dict:
    total = len(books)
    with_edition = sum(1 for b in books if b.get("editions"))
    empty_editions = total - with_edition
    return {
        "label": label,
        "total_books": total,
        "with_matching_edition": with_edition,
        "empty_editions": empty_editions,
        "empty_edition_titles_sample": [
            b["title"] for b in books if not b.get("editions")
        ][:10],
    }


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--author-id", type=int, default=204214,
                   help="Hardcover author ID (default: 204214 = Brandon Sanderson)")
    p.add_argument("--name", default="Brandon Sanderson",
                   help="Author display name (informational)")
    p.add_argument("--limit", type=int, default=100,
                   help="Per-page limit (default: 100)")
    p.add_argument("--max-pages", type=int, default=20,
                   help="Cap on pages to fetch (default: 20 → up to 2000 books)")
    p.add_argument("--out-dir", default="/tmp/seshat-hardcover-probe",
                   help="Where to write artifacts")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Hardcover key from settings (encrypted store or settings.json fallback).
    try:
        from app.secrets import get_secret as _get_secret
        key = await _get_secret("hardcover_api_key")
    except Exception:
        key = None
    if not key:
        s = load_settings()
        key = s.get("hardcover_api_key", "")
    if not key:
        print("ERROR: no hardcover_api_key configured", file=sys.stderr)
        sys.exit(1)

    print(f"Probing Hardcover audiobook filter for '{args.name}' (id={args.author_id})")
    print(f"  audiobook format_ids = [2]")
    print()

    async with httpx.AsyncClient() as client:
        # Fetch all pages with both queries.
        async def _paginate(query: str, label: str) -> list[dict]:
            all_books: list[dict] = []
            for page in range(args.max_pages):
                vars = {
                    "id": args.author_id,
                    "limit": args.limit,
                    "offset": page * args.limit,
                    "format_ids": [2],
                }
                body = await _fetch(client, key, query, vars)
                authors = body.get("data", {}).get("authors", [])
                if not authors:
                    break
                contribs = authors[0].get("contributions", [])
                if not contribs:
                    break
                page_books = [c["book"] for c in contribs if c.get("book")]
                all_books.extend(page_books)
                print(f"  [{label}] page {page+1}: +{len(page_books)} books (running total {len(all_books)})")
                if len(page_books) < args.limit:
                    break
            return all_books

        print("=== Variant A: current production query (contributions unfiltered) ===")
        current_books = await _paginate(QUERY_CURRENT, "current")
        print()
        print("=== Variant B: GraphQL-side contributions filter ===")
        graphql_books = await _paginate(QUERY_GRAPHQL_FILTER, "graphql")

    current_summary = _summarize("current", current_books)
    graphql_summary = _summarize("graphql-filter", graphql_books)

    # Diff: do both variants reach the same books-with-edition set?
    current_with = {b["id"] for b in current_books if b.get("editions")}
    graphql_with = {b["id"] for b in graphql_books if b.get("editions")}
    missing_in_graphql = current_with - graphql_with
    extra_in_graphql = graphql_with - current_with

    print()
    print("=== Summary ===")
    print(json.dumps({
        "current_query": current_summary,
        "graphql_filter_query": graphql_summary,
        "diff": {
            "books_with_audio_edition_in_current_but_NOT_graphql": len(missing_in_graphql),
            "books_with_audio_edition_in_graphql_but_NOT_current": len(extra_in_graphql),
            "matched_set_size": len(current_with & graphql_with),
        },
    }, indent=2))

    summary_path = out_dir / "result.json"
    summary_path.write_text(json.dumps({
        "author_id": args.author_id,
        "author_name": args.name,
        "current": current_summary,
        "graphql_filter": graphql_summary,
        "diff": {
            "missing_in_graphql_count": len(missing_in_graphql),
            "extra_in_graphql_count": len(extra_in_graphql),
            "matched_set_size": len(current_with & graphql_with),
        },
        "missing_in_graphql_sample_ids": list(missing_in_graphql)[:20],
        "extra_in_graphql_sample_ids": list(extra_in_graphql)[:20],
    }, indent=2))
    print()
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
