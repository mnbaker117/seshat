"""
Calibre sink: add books via `calibredb add`.

Uses the `calibredb` CLI rather than Calibre's content server API
because:
  1. calibredb is always available in any Calibre installation
  2. It handles duplicate detection, format conversion, and metadata
     enrichment automatically
  3. No auth setup needed (it talks to the library directory directly)

The library path must be configured in settings.json or via the
CALIBRE_LIBRARY_PATH environment variable.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from app.metadata.extract import BookMetadata
from app.sinks.base import SinkResult

_log = logging.getLogger("seshat.sinks")

# calibredb binary name. Can be overridden for testing.
CALIBREDB_CMD = "calibredb"


class CalibreSink:
    """Delivers book files to a Calibre library via calibredb."""

    name = "calibre"

    def __init__(self, library_path: str):
        self.library_path = library_path

    async def deliver(
        self,
        file_path: str,
        metadata: BookMetadata,
    ) -> SinkResult:
        """Add a book file to the Calibre library.

        Uses `calibredb add --library-path <path> <file>`.
        Optionally sets title/author if metadata is available.
        """
        if not self.library_path:
            return SinkResult(
                success=False,
                sink_name=self.name,
                error="Calibre library path not configured",
            )

        path = Path(file_path)
        if not path.exists():
            return SinkResult(
                success=False,
                sink_name=self.name,
                error=f"file not found: {file_path}",
            )

        cmd = [
            CALIBREDB_CMD, "add",
            "--library-path", self.library_path,
        ]

        # Set metadata if available.
        if metadata.title:
            cmd.extend(["--title", metadata.title])
        if metadata.author:
            cmd.extend(["--authors", metadata.author])
        if metadata.series:
            cmd.extend(["--series", metadata.series])
        if metadata.series_index:
            cmd.extend(["--series-index", metadata.series_index])
        if metadata.isbn:
            cmd.extend(["--isbn", metadata.isbn])

        cmd.append(str(path))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60
            )

            output = stdout.decode("utf-8", errors="replace").strip()
            err_output = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode == 0:
                _log.info("calibredb add succeeded: %s", output or path.name)
                return SinkResult(
                    success=True,
                    sink_name=self.name,
                    detail=output or f"added {path.name}",
                )

            full_error = f"exit {proc.returncode}: {err_output or output}"
            _log.warning("calibredb add failed: %s", full_error)
            return SinkResult(
                success=False,
                sink_name=self.name,
                error=full_error,
            )
        except FileNotFoundError:
            return SinkResult(
                success=False,
                sink_name=self.name,
                error="calibredb not found — is Calibre installed?",
            )
        except asyncio.TimeoutError:
            return SinkResult(
                success=False,
                sink_name=self.name,
                error="calibredb timed out after 60s",
            )
        except Exception as e:
            _log.exception("calibredb add raised")
            return SinkResult(
                success=False,
                sink_name=self.name,
                error=f"{type(e).__name__}: {e}",
            )
