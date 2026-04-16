"""
Unit tests for the CWA (Calibre-Web-Automated) sink.
"""
from pathlib import Path

from app.metadata.extract import BookMetadata
from app.sinks.cwa import CWASink


class TestCWASink:
    async def test_drops_file_flat(self, tmp_path):
        src = tmp_path / "staging" / "book.epub"
        src.parent.mkdir()
        src.write_bytes(b"epub content")
        ingest = tmp_path / "cwa-ingest"

        sink = CWASink(str(ingest))
        result = await sink.deliver(str(src), BookMetadata())

        assert result.success is True
        assert result.sink_name == "cwa"
        # CWA expects flat drops — file should be directly in ingest dir.
        assert (ingest / "book.epub").exists()

    async def test_avoids_overwrite(self, tmp_path):
        src = tmp_path / "book.epub"
        src.write_bytes(b"new")
        ingest = tmp_path / "cwa-ingest"
        ingest.mkdir()
        (ingest / "book.epub").write_bytes(b"pending")

        sink = CWASink(str(ingest))
        result = await sink.deliver(str(src), BookMetadata())

        assert result.success is True
        assert (ingest / "book.epub").read_bytes() == b"pending"
        assert (ingest / "book_1.epub").exists()

    async def test_no_ingest_path_fails(self):
        sink = CWASink("")
        result = await sink.deliver("/some/book.epub", BookMetadata())
        assert result.success is False
        assert "not configured" in result.error

    async def test_missing_file_fails(self, tmp_path):
        sink = CWASink(str(tmp_path))
        result = await sink.deliver("/nope/book.epub", BookMetadata())
        assert result.success is False
        assert "not found" in result.error

    async def test_creates_ingest_dir(self, tmp_path):
        src = tmp_path / "book.epub"
        src.write_bytes(b"content")
        ingest = tmp_path / "deep" / "ingest"

        sink = CWASink(str(ingest))
        result = await sink.deliver(str(src), BookMetadata())

        assert result.success is True
        assert ingest.exists()
