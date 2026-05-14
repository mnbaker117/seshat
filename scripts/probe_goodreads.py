#!/usr/bin/env python3
"""
Diagnostic probe for v2.13.0 Stage 6 Goodreads Cloudflare bypass.

Runs a live, end-to-end exercise of the production `goodreads_session`
module against one or more Goodreads book IDs from inside the
container (where curl_cffi is installed) — establishes a baseline of
what the in-app probe + scan should expect.

Usage (inside the Seshat container):

    # Single book probe
    docker exec Seshat python /app/scripts/probe_goodreads.py --single 237832459

    # Burst probe (10 books from the canonical v2.13.0 pool)
    docker exec Seshat python /app/scripts/probe_goodreads.py --burst

    # Burst probe with a custom list
    docker exec Seshat python /app/scripts/probe_goodreads.py --burst 33 5907 1885

Output:
  - Stdout: human-readable per-request + aggregate summary
  - --out-dir defaults to /tmp/seshat-goodreads-probe/
      response_<id>_<n>.html      — captured body for each request
      summary.json                 — aggregate stats

This script does NOT modify any DB state. It uses the production
`goodreads_session` module, so the soft-block detection +
runtime-state flag transitions are identical to what the app does
during a live scan. Mark active afterwards from the Settings UI if
you want to clear any state-flag side effects.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Bootstrap the seshat package import path when invoked directly.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.metadata import goodreads_session as gr  # noqa: E402


# Canonical v2.13.0 probe pool (sourced from Mark's prod library
# 2026-05-14). Mirrors the default in
# `app/routers/goodreads_session.py` so host-side runs and in-app
# burst probes hit the same books for comparable results.
_DEFAULT_POOL: list[tuple[str, str, str]] = [
    ("237832459", "The Devil's Peak", "Greig Beck"),
    ("213076829", "Returner's Defiance", "Bruce Sentar"),
    ("60548283",  "Survivors 3: A Lost World Harem", "Jack Porter"),
    ("34403860",  "Sufficiently Advanced Magic", "Andrew Rowe"),
    ("228713175", "Phoenix Trials (Bloodline of the Phoenix Book 4)", "S. D. McKittrick"),
    ("40581053",  "Earth Unrelenting", "M. R. Forbes"),
    ("237894285", "These Heroines Are So High Maintenance", "Virgil Knightley"),
    ("243255290", "Trailer Park Bikini Vampires 2", "Virgil Knightley"),
    ("48593270",  "Metal Mage 8", "Eric Vall"),
    ("35583546",  "Defiance", "Joel Shepherd"),
]


async def _probe_one(book_id: str, label: str, out_dir: Path, idx: int) -> dict:
    """Fetch one /book/show/{id} via the production session. Capture
    body to disk for post-hoc inspection. Returns a summary dict."""
    session = await gr.get_session()
    started = time.monotonic()
    try:
        resp = await session.get(f"https://www.goodreads.com/book/show/{book_id}")
    except Exception as e:
        wall_ms = int((time.monotonic() - started) * 1000)
        print(f"  [{idx}] {book_id} {label!r:60} TRANSPORT ERROR {wall_ms}ms — {e}")
        return {
            "goodreads_id": book_id, "label": label,
            "status": 0, "body_size_kb": 0, "wall_ms": wall_ms,
            "soft_blocked": False, "error": str(e),
        }
    wall_ms = int((time.monotonic() - started) * 1000)
    body = getattr(resp, "content", b"") or b""
    body_kb = round(len(body) / 1024.0, 2)
    status = int(getattr(resp, "status_code", 0))
    soft = gr.is_cloudflare_soft_block(resp)
    # Write body to disk for inspection.
    out_path = out_dir / f"response_{book_id}_{idx:02d}.html"
    try:
        out_path.write_bytes(body)
    except Exception:
        pass
    flag = "SOFT-BLOCK" if soft else "ok"
    print(f"  [{idx}] {book_id} {label!r:60} {status} {body_kb:>7.2f}KB {wall_ms:>6}ms {flag}")
    return {
        "goodreads_id": book_id, "label": label,
        "status": status, "body_size_kb": body_kb,
        "wall_ms": wall_ms, "soft_blocked": soft,
    }


async def main_async(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build the work list.
    if args.single:
        pool = [(args.single, "(user-supplied)", "")]
    elif args.book_ids:
        pool = [(bid, "(user-supplied)", "") for bid in args.book_ids]
    else:
        pool = _DEFAULT_POOL

    print(f"goodreads probe → {len(pool)} request(s)")
    print(f"  rate_limit = {gr._DEFAULT_RATE_LIMIT}s + 0-1s jitter")
    print(f"  out_dir    = {out_dir}")
    print(f"  initial state = {gr.get_session_state()}")
    print()

    started = time.monotonic()
    per_request: list[dict] = []
    for idx, (bid, title, author) in enumerate(pool, start=1):
        label = title if not author else f"{title} — {author}"
        per_request.append(await _probe_one(bid, label, out_dir, idx))
    total_s = round(time.monotonic() - started, 2)

    # Aggregate.
    status_dist: dict[int, int] = {}
    soft_blocks = 0
    body_kb_total = 0.0
    for r in per_request:
        status_dist[r["status"]] = status_dist.get(r["status"], 0) + 1
        if r["soft_blocked"]:
            soft_blocks += 1
        body_kb_total += r["body_size_kb"]
    mean_kb = round(body_kb_total / len(per_request), 2) if per_request else 0.0

    summary = {
        "mode": "single" if args.single else "burst",
        "requests": len(per_request),
        "status_distribution": status_dist,
        "soft_blocks": soft_blocks,
        "total_wall_s": total_s,
        "mean_body_kb": mean_kb,
        "state_after": gr.get_session_state(),
        "per_request": per_request,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print()
    print("─" * 60)
    print(f"  status_distribution = {status_dist}")
    print(f"  soft_blocks         = {soft_blocks} / {len(per_request)}")
    print(f"  total_wall_s        = {total_s}")
    print(f"  mean_body_kb        = {mean_kb}")
    print(f"  state_after         = {gr.get_session_state()}")
    print(f"  summary             = {out_dir / 'summary.json'}")
    print("─" * 60)

    await gr.close_session()
    return 0 if soft_blocks == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe Goodreads via the production goodreads_session module.",
    )
    parser.add_argument(
        "--single", type=str, default=None,
        help="One goodreads_id to probe (overrides --burst).",
    )
    parser.add_argument(
        "--burst", action="store_true",
        help="Run the full canonical pool (or `book_ids` positional list).",
    )
    parser.add_argument(
        "book_ids", nargs="*",
        help="Optional list of goodreads_ids for burst mode.",
    )
    parser.add_argument(
        "--out-dir", type=str, default="/tmp/seshat-goodreads-probe",
    )
    args = parser.parse_args()
    if not args.single and not args.burst and not args.book_ids:
        # No mode specified → default to burst against canonical pool.
        args.burst = True
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
