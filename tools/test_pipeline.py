#!/usr/bin/env python3
"""
End-to-end pipeline test harness.

Pushes a book file (epub, m4b, etc.) through the full post-download
pipeline without needing a real IRC announce or qBit download. Tests
the pipeline in isolation:

    source file → staging → metadata extraction → sink → auto-train

Usage:
    # Test with a local file, folder sink:
    python tools/test_pipeline.py /path/to/book.epub --sink folder --folder-path /tmp/test-library

    # Test with a file on the Unraid box via SSH:
    python tools/test_pipeline.py --remote "/mnt/user/downloads/[mam-complete]/some-book.epub" \\
        --sink folder --folder-path /tmp/test-library

    # Test with Calibre sink:
    python tools/test_pipeline.py /path/to/book.epub --sink calibre --calibre-path /path/to/calibre/library

    # Dry run (stage + metadata only, no sink delivery):
    python tools/test_pipeline.py /path/to/book.epub --dry-run
"""
import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Add repo root to path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.metadata.extract import extract as extract_metadata
from app.orchestrator.file_copier import copy_to_staging, find_book_files
from app.sinks.calibre import CalibreSink
from app.sinks.folder import FolderSink


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_log = logging.getLogger("test_pipeline")


def main():
    parser = argparse.ArgumentParser(
        description="Test the post-download pipeline end-to-end",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Path to a local book file or directory",
    )
    parser.add_argument(
        "--remote",
        help="Remote path on Unraid (fetched via scp)",
    )
    parser.add_argument(
        "--sink",
        choices=["calibre", "folder", "none"],
        default="none",
        help="Which sink to deliver to (default: none = dry run)",
    )
    parser.add_argument(
        "--calibre-path",
        default="",
        help="Path to Calibre library (for calibre sink)",
    )
    parser.add_argument(
        "--folder-path",
        default="",
        help="Path to target folder (for folder sink)",
    )
    parser.add_argument(
        "--staging",
        default="",
        help="Staging directory (default: auto temp dir)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stage + metadata only, skip sink delivery",
    )
    parser.add_argument(
        "--torrent-name",
        default="",
        help="Override torrent name for staging dir",
    )

    args = parser.parse_args()

    if args.dry_run:
        args.sink = "none"

    asyncio.run(_run(args))


async def _run(args):
    # ── Step 0: resolve source file ─────────────────────────
    source_path = None

    if args.remote:
        _log.info("Fetching remote file: %s", args.remote)
        tmp = Path(tempfile.mkdtemp(prefix="seshat-test-"))
        filename = Path(args.remote).name
        local_copy = tmp / filename
        result = subprocess.run(
            ["scp", f"deepstonecrypt:{args.remote}", str(local_copy)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            _log.error("scp failed: %s", result.stderr)
            return
        source_path = local_copy
        _log.info("Fetched to: %s", source_path)
    elif args.source:
        source_path = Path(args.source)
    else:
        _log.error("Provide either a local path or --remote")
        return

    if not source_path.exists():
        _log.error("Source not found: %s", source_path)
        return

    torrent_name = args.torrent_name or source_path.stem

    # ── Step 1: find book files ─────────────────────────────
    books = find_book_files(source_path)
    if not books:
        _log.error("No book files found in %s", source_path)
        return

    _log.info("Found %d book file(s):", len(books))
    for b in books:
        _log.info("  %s (%d bytes)", b.name, b.stat().st_size)

    # ── Step 2: copy to staging ─────────────────────────────
    staging_dir = Path(args.staging) if args.staging else Path(tempfile.mkdtemp(prefix="seshat-staging-"))
    _log.info("Staging directory: %s", staging_dir)

    copy_result = copy_to_staging(source_path, staging_dir, torrent_name)
    if not copy_result.success:
        _log.error("Staging failed: %s", copy_result.error)
        return

    _log.info("Staged %d file(s) to: %s", copy_result.files_copied, copy_result.staged_path)
    _log.info("Primary file: %s (format: %s)", copy_result.book_filename, copy_result.book_format)

    # ── Step 3: extract metadata ────────────────────────────
    primary_path = Path(copy_result.staged_path) / copy_result.book_filename
    metadata = extract_metadata(primary_path)

    _log.info("─── Extracted Metadata ───")
    _log.info("  Title:    %s", metadata.title or "(none)")
    _log.info("  Author:   %s", metadata.author or "(none)")
    _log.info("  Series:   %s", metadata.series or "(none)")
    _log.info("  Index:    %s", metadata.series_index or "(none)")
    _log.info("  Language: %s", metadata.language or "(none)")
    _log.info("  Publisher: %s", metadata.publisher or "(none)")
    _log.info("  ISBN:     %s", metadata.isbn or "(none)")
    _log.info("  Format:   %s", metadata.format or "(none)")

    # ── Step 4: deliver to sink ─────────────────────────────
    if args.sink == "none":
        _log.info("─── Dry run — skipping sink delivery ───")
        _log.info("Pipeline test complete (staging + metadata only)")
        return

    if args.sink == "calibre":
        sink = CalibreSink(args.calibre_path)
    elif args.sink == "folder":
        sink = FolderSink(args.folder_path)
    else:
        _log.error("Unknown sink: %s", args.sink)
        return

    _log.info("Delivering to sink: %s", sink.name)
    sink_result = await sink.deliver(str(primary_path), metadata)

    if sink_result.success:
        _log.info("Sink delivery succeeded: %s", sink_result.detail)
    else:
        _log.error("Sink delivery failed: %s", sink_result.error)
        return

    _log.info("─── Pipeline test complete ───")
    _log.info("  Source:   %s", source_path)
    _log.info("  Staged:   %s", copy_result.staged_path)
    _log.info("  Metadata: title=%s, author=%s", metadata.title, metadata.author)
    _log.info("  Sink:     %s → %s", sink.name, sink_result.detail)


if __name__ == "__main__":
    main()
