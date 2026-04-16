"""
Unit tests for the folder sink.
"""
from pathlib import Path

from app.metadata.extract import BookMetadata
from app.sinks.folder import FolderSink


class TestFolderSink:
    async def test_copies_file(self, tmp_path):
        src = tmp_path / "staging" / "book.epub"
        src.parent.mkdir()
        src.write_bytes(b"epub content")
        target = tmp_path / "library"

        sink = FolderSink(str(target))
        result = await sink.deliver(str(src), BookMetadata())

        assert result.success is True
        assert result.sink_name == "folder"
        assert (target / "book.epub").exists()
        assert (target / "book.epub").read_bytes() == b"epub content"

    async def test_avoids_overwrite(self, tmp_path):
        src = tmp_path / "staging" / "book.epub"
        src.parent.mkdir()
        src.write_bytes(b"new content")
        target = tmp_path / "library"
        target.mkdir()
        (target / "book.epub").write_bytes(b"old content")

        sink = FolderSink(str(target))
        result = await sink.deliver(str(src), BookMetadata())

        assert result.success is True
        # Original preserved.
        assert (target / "book.epub").read_bytes() == b"old content"
        # New file has a suffix.
        assert (target / "book_1.epub").exists()

    async def test_missing_file_fails(self, tmp_path):
        sink = FolderSink(str(tmp_path / "library"))
        result = await sink.deliver("/nope/book.epub", BookMetadata())
        assert result.success is False
        assert "not found" in result.error

    async def test_no_target_path_fails(self):
        sink = FolderSink("")
        result = await sink.deliver("/some/book.epub", BookMetadata())
        assert result.success is False
        assert "not configured" in result.error

    async def test_creates_target_dir(self, tmp_path):
        src = tmp_path / "book.epub"
        src.write_bytes(b"content")
        target = tmp_path / "deep" / "nested" / "dir"

        sink = FolderSink(str(target))
        result = await sink.deliver(str(src), BookMetadata())

        assert result.success is True
        assert target.exists()
