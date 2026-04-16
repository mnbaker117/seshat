"""
Folder sink: copy books to a target directory.

The simplest sink — just copies the file to a configured directory.
Useful for users who manage their library manually, or as a fallback
when Calibre isn't available. Also used for audiobook files that go
to Audiobookshelf's watch directory.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.metadata.extract import BookMetadata
from app.sinks.base import SinkResult

_log = logging.getLogger("seshat.sinks")


class FolderSink:
    """Delivers book files by copying them to a directory."""

    name = "folder"

    def __init__(self, target_path: str):
        self.target_path = target_path

    async def deliver(
        self,
        file_path: str,
        metadata: BookMetadata,
    ) -> SinkResult:
        """Copy a book file to the target directory."""
        if not self.target_path:
            return SinkResult(
                success=False,
                sink_name=self.name,
                error="folder sink path not configured",
            )

        src = Path(file_path)
        if not src.exists():
            return SinkResult(
                success=False,
                sink_name=self.name,
                error=f"file not found: {file_path}",
            )

        target_dir = Path(self.target_path)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / src.name

            # Avoid overwriting existing files.
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                counter = 1
                while dest.exists():
                    dest = target_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            shutil.copy2(str(src), str(dest))
            _log.info("folder sink: copied %s → %s", src.name, dest)
            return SinkResult(
                success=True,
                sink_name=self.name,
                detail=str(dest),
            )
        except Exception as e:
            _log.exception("folder sink copy failed")
            return SinkResult(
                success=False,
                sink_name=self.name,
                error=f"{type(e).__name__}: {e}",
            )
