"""
File copier: extract book files from qBit download dir to staging.

When a torrent finishes downloading, qBit leaves the files in its
download directory (e.g. `/downloads/[mam-reseed]/Book Name/`). The
copier scans that directory for book files (epub, m4b, pdf, cbz, etc.),
copies them to the staging directory, and updates the pipeline_run row
with the staged path and detected format.

Design choices:
  - COPY, not move. The original stays in place for seeding.
  - Only recognized book formats are copied. Ancillary files (.nfo,
    .txt, .jpg) are left behind.
  - If multiple book files exist in the torrent (e.g., a series pack),
    each gets its own copy. The pipeline_run row tracks the "primary"
    file (largest by size).
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_log = logging.getLogger("seshat.orchestrator.file_copier")

# Recognized book file extensions (lowercase, no dot).
BOOK_EXTENSIONS = frozenset({
    "epub", "mobi", "azw", "azw3", "pdf",
    "m4b", "mp3",           # audiobook formats
    "cbz", "cbr",           # comics
    "lit", "fb2", "djvu",
})


@dataclass(frozen=True)
class CopyResult:
    """Outcome of a file copy operation."""

    success: bool
    staged_path: Optional[str] = None
    book_filename: Optional[str] = None
    book_format: Optional[str] = None
    files_copied: int = 0
    error: Optional[str] = None


def find_book_files(source_dir: Path) -> list[Path]:
    """Find all book files in a directory tree.

    Returns files sorted by size descending (largest first) so the
    caller can use [0] as the "primary" file.
    """
    if not source_dir.exists():
        return []

    if source_dir.is_file():
        ext = source_dir.suffix.lstrip(".").lower()
        return [source_dir] if ext in BOOK_EXTENSIONS else []

    found: list[Path] = []
    for path in source_dir.rglob("*"):
        if path.is_file():
            ext = path.suffix.lstrip(".").lower()
            if ext in BOOK_EXTENSIONS:
                found.append(path)

    return sorted(found, key=lambda p: p.stat().st_size, reverse=True)


def copy_to_staging(
    source_dir: Path,
    staging_dir: Path,
    torrent_name: str,
    *,
    explicit_files: Optional[list[Path]] = None,
) -> CopyResult:
    """Copy book files from source to staging.

    When `explicit_files` is provided the copier uses exactly that
    list — typically populated from qBit's `/torrents/files` response
    so we copy only what belongs to this specific torrent, even when
    the save_path also contains files from other torrents. Without
    it, `source_dir` is scanned recursively (legacy behavior used
    when the client can't report its file list).

    Creates a subdirectory under staging_dir named after the torrent.
    Returns info about the primary (largest) book file.

    This is a synchronous function because file I/O on local disk is
    fast and shutil.copy2 doesn't have an async variant. The caller
    should run it in a thread pool if needed.
    """
    source_dir = Path(source_dir)
    staging_str = str(staging_dir).strip() if staging_dir else ""
    if not staging_str or staging_str == ".":
        return CopyResult(success=False, error="staging directory not configured")
    staging_dir = Path(staging_str)

    try:
        if explicit_files is not None:
            # Filter the explicit list to existing book-format files
            # and sort largest-first so the primary selection matches
            # the find_book_files ordering.
            book_files = sorted(
                [p for p in explicit_files
                 if p.is_file() and p.suffix.lstrip(".").lower() in BOOK_EXTENSIONS],
                key=lambda p: p.stat().st_size, reverse=True,
            )
        else:
            book_files = find_book_files(source_dir)
        if not book_files:
            return CopyResult(
                success=False,
                error=f"no book files found in {source_dir}",
            )

        # Create a staging subdirectory for this torrent.
        dest_dir = staging_dir / _safe_dirname(torrent_name)
        dest_dir.mkdir(parents=True, exist_ok=True)

        copied = 0
        primary_dest: Optional[Path] = None

        for src_file in book_files:
            dest_file = dest_dir / src_file.name
            # Avoid overwriting if two files have the same name.
            if dest_file.exists():
                stem = dest_file.stem
                suffix = dest_file.suffix
                counter = 1
                while dest_file.exists():
                    dest_file = dest_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            shutil.copy2(str(src_file), str(dest_file))
            copied += 1

            if primary_dest is None:
                primary_dest = dest_file

        primary = primary_dest or dest_dir
        fmt = primary.suffix.lstrip(".").lower() if primary.is_file() else ""

        _log.info(
            "copied %d book file(s) to staging: %s → %s",
            copied, source_dir, dest_dir,
        )

        return CopyResult(
            success=True,
            staged_path=str(dest_dir),
            book_filename=primary.name if primary.is_file() else None,
            book_format=fmt or None,
            files_copied=copied,
        )
    except Exception as e:
        _log.exception("file copy failed: %s → %s", source_dir, staging_dir)
        return CopyResult(success=False, error=f"{type(e).__name__}: {e}")


def _safe_dirname(name: str) -> str:
    """Sanitize a torrent name for use as a directory name."""
    # Replace filesystem-unsafe characters.
    unsafe = '<>:"/\\|?*'
    result = name
    for ch in unsafe:
        result = result.replace(ch, "_")
    return result.strip(". ") or "unnamed"
