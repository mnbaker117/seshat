"""
Audiobookshelf sink.

Delivers audiobook files to Audiobookshelf's watch/import directory.
ABS auto-imports files dropped into its configured library folder,
organized by author → book title. We trigger an explicit rescan via
the ABS API after the drop so the book appears in the UI immediately
(ABS's filesystem watcher catches it eventually, but the API call
takes ~200ms and saves the user refreshing until it shows up).

This is a thin specialization of the folder sink — the directory
structure matches what ABS expects for a clean auto-import.
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

    def __init__(
        self,
        library_path: str,
        *,
        abs_base_url: str = "",
        abs_api_key: str = "",
        abs_library_id: str = "",
    ):
        """Construct the sink.

        `library_path` is mandatory — that's the folder we copy into.
        The three `abs_*` parameters are optional; when all three are
        set, the sink triggers a library rescan via the ABS REST API
        after the file copy so the book shows up immediately. A
        missing API-side config just means ABS's filesystem watcher
        picks it up on its own timer (typically ≤ 60 seconds).
        """
        self.library_path = library_path
        self.abs_base_url = abs_base_url.rstrip("/")
        self.abs_api_key = abs_api_key
        self.abs_library_id = abs_library_id

    async def deliver(
        self,
        file_path: str,
        metadata: BookMetadata,
    ) -> SinkResult:
        """Copy an audiobook file into Audiobookshelf's directory structure.

        Organizes as: library_path / Author / Title / filename
        Falls back to "Unknown Author" / filename stem if metadata is missing.

        If `abs_base_url`, `abs_api_key`, and `abs_library_id` are all
        set, a POST /api/libraries/{id}/scan fires after the copy
        succeeds — failures on the scan call are logged but never
        propagate into a failed SinkResult. The copy is the
        authoritative outcome; the scan is best-effort UX polish.
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
        except Exception as e:
            _log.exception("audiobookshelf sink copy failed")
            return SinkResult(
                success=False,
                sink_name=self.name,
                error=f"{type(e).__name__}: {e}",
            )

        await self._maybe_trigger_scan()

        return SinkResult(
            success=True,
            sink_name=self.name,
            detail=str(dest),
        )

    async def _maybe_trigger_scan(self) -> None:
        """Fire the ABS library-scan endpoint if we're configured for it.

        Silent on failure — the drop already succeeded, and ABS's
        watcher will eventually pick up the new files regardless.
        """
        if not (self.abs_base_url and self.abs_api_key and self.abs_library_id):
            return
        try:
            from app.library_apps.audiobookshelf import AudiobookshelfClient
            client = AudiobookshelfClient(self.abs_base_url, self.abs_api_key)
            ok = await client.trigger_scan(self.abs_library_id)
            if ok:
                _log.info(
                    "audiobookshelf sink: triggered scan on library %s",
                    self.abs_library_id,
                )
            else:
                _log.info(
                    "audiobookshelf sink: scan POST returned non-2xx for library %s",
                    self.abs_library_id,
                )
        except Exception as e:
            _log.info(
                "audiobookshelf sink: scan trigger failed (%s: %s) — "
                "relying on ABS watcher",
                type(e).__name__, e,
            )


def _safe_name(name: str) -> str:
    """Sanitize a string for use as a directory name."""
    unsafe = '<>:"/\\|?*'
    result = name
    for ch in unsafe:
        result = result.replace(ch, "_")
    return result.strip(". ") or "unknown"
