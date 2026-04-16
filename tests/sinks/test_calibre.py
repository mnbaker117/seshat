"""
Unit tests for the Calibre sink.

Tests use a fake calibredb script that echoes its arguments so we
can verify the correct CLI invocation without needing Calibre installed.
"""
import os
import stat
from pathlib import Path

import pytest

from app.metadata.extract import BookMetadata
from app.sinks import calibre
from app.sinks.calibre import CalibreSink


@pytest.fixture
def fake_calibredb(tmp_path, monkeypatch):
    """Create a fake calibredb script that logs its args and exits 0."""
    script = tmp_path / "calibredb"
    script.write_text(
        '#!/bin/sh\necho "Added book: $@"\nexit 0\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(calibre, "CALIBREDB_CMD", str(script))
    return script


@pytest.fixture
def failing_calibredb(tmp_path, monkeypatch):
    """Create a fake calibredb that exits with error."""
    script = tmp_path / "calibredb"
    script.write_text(
        '#!/bin/sh\necho "Error: duplicate book" >&2\nexit 1\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(calibre, "CALIBREDB_CMD", str(script))
    return script


class TestCalibreSink:
    async def test_successful_add(self, tmp_path, fake_calibredb):
        book = tmp_path / "book.epub"
        book.write_bytes(b"epub content")
        library = tmp_path / "calibre_lib"
        library.mkdir()

        sink = CalibreSink(str(library))
        result = await sink.deliver(str(book), BookMetadata(title="Test"))

        assert result.success is True
        assert result.sink_name == "calibre"

    async def test_passes_metadata_flags(self, tmp_path, fake_calibredb):
        book = tmp_path / "book.epub"
        book.write_bytes(b"content")
        library = tmp_path / "lib"
        library.mkdir()

        meta = BookMetadata(
            title="The Way of Kings",
            author="Brandon Sanderson",
            series="Stormlight Archive",
            series_index="1",
            isbn="9780765326355",
        )
        sink = CalibreSink(str(library))
        result = await sink.deliver(str(book), meta)

        assert result.success is True
        # The fake script echoes all args, so the detail contains them.
        assert "The Way of Kings" in result.detail
        assert "Brandon Sanderson" in result.detail

    async def test_failed_add(self, tmp_path, failing_calibredb):
        book = tmp_path / "book.epub"
        book.write_bytes(b"content")

        sink = CalibreSink(str(tmp_path))
        result = await sink.deliver(str(book), BookMetadata())

        assert result.success is False
        assert "exit 1" in result.error

    async def test_no_library_path(self, tmp_path):
        sink = CalibreSink("")
        result = await sink.deliver(str(tmp_path / "book.epub"), BookMetadata())
        assert result.success is False
        assert "not configured" in result.error

    async def test_missing_file(self, tmp_path, fake_calibredb):
        sink = CalibreSink(str(tmp_path))
        result = await sink.deliver("/nope/book.epub", BookMetadata())
        assert result.success is False
        assert "not found" in result.error

    async def test_calibredb_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(calibre, "CALIBREDB_CMD", "/nonexistent/calibredb")
        book = tmp_path / "book.epub"
        book.write_bytes(b"content")

        sink = CalibreSink(str(tmp_path))
        result = await sink.deliver(str(book), BookMetadata())

        assert result.success is False
        assert "not found" in result.error
