"""
Audiobookshelf sink.

Delivers audiobook files to Audiobookshelf's watch/import directory.
Audiobookshelf auto-imports files dropped into its configured library
folder, organized by author → book title.

This is a thin specialization of the folder sink that organizes files
into the `Author/Title/` directory structure that Audiobookshelf
expects for clean auto-import.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.metadata.extract import BookMetadata
from app.sinks.base import SinkResult

_log = logging.getLogger("seshat.sinks")


class AudiobookshelfSink:
    """Delivers audiobook files to Audiobookshelf's library directory."""

    name = "audiobookshelf"

    def __init__(self, library_path: str):
        self.library_path = library_path

    async def deliver(
        self,
        file_path: str,
        metadata: BookMetadata,
    ) -> SinkResult:
        """Copy an audiobook file into Audiobookshelf's directory structure.

        Organizes as: library_path / Author / Title / filename
        Falls back to "Unknown Author" / filename stem if metadata is missing.
        """
        if not self.library_path:
            return SinkResult(
                success=False,
                sink_name=self.name,
                error="Audiobookshelf library path not configured",
            )

        src = Path(file_path)
        if not src.exists():
            return SinkResult(
                success=False,
                sink_name=self.name,
                error=f"file not found: {file_path}",
            )

        author = metadata.author or "Unknown Author"
        title = metadata.title or metadata.series or src.stem

        # Sanitize directory names.
        author_dir = _safe_name(author)
        title_dir = _safe_name(title)

        target_dir = Path(self.library_path) / author_dir / title_dir

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / src.name

            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                counter = 1
                while dest.exists():
                    dest = target_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            shutil.copy2(str(src), str(dest))
            _log.info(
                "audiobookshelf sink: copied %s → %s",
                src.name, dest,
            )
            return SinkResult(
                success=True,
                sink_name=self.name,
                detail=str(dest),
            )
        except Exception as e:
            _log.exception("audiobookshelf sink copy failed")
            return SinkResult(
                success=False,
                sink_name=self.name,
                error=f"{type(e).__name__}: {e}",
            )


def _safe_name(name: str) -> str:
    """Sanitize a string for use as a directory name."""
    unsafe = '<>:"/\\|?*'
    result = name
    for ch in unsafe:
        result = result.replace(ch, "_")
    return result.strip(". ") or "unknown"
