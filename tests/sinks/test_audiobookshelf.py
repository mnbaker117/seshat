"""
Unit tests for the Audiobookshelf sink.
"""
from pathlib import Path

from app.metadata.extract import BookMetadata
from app.sinks.audiobookshelf import AudiobookshelfSink


class TestAudiobookshelfSink:
    async def test_organizes_by_author_and_title(self, tmp_path):
        src = tmp_path / "staging" / "book.m4b"
        src.parent.mkdir()
        src.write_bytes(b"audiobook content")
        library = tmp_path / "abs-library"

        sink = AudiobookshelfSink(str(library))
        meta = BookMetadata(author="Brandon Sanderson", title="The Way of Kings")
        result = await sink.deliver(str(src), meta)

        assert result.success is True
        assert result.sink_name == "audiobookshelf"
        expected = library / "Brandon Sanderson" / "The Way of Kings" / "book.m4b"
        assert expected.exists()

    async def test_falls_back_to_unknown_author(self, tmp_path):
        src = tmp_path / "book.m4b"
        src.write_bytes(b"content")
        library = tmp_path / "abs-library"

        sink = AudiobookshelfSink(str(library))
        result = await sink.deliver(str(src), BookMetadata(title="Some Title"))

        assert result.success is True
        assert (library / "Unknown Author" / "Some Title" / "book.m4b").exists()

    async def test_falls_back_to_filename_stem(self, tmp_path):
        src = tmp_path / "My Audiobook.m4b"
        src.write_bytes(b"content")
        library = tmp_path / "abs-library"

        sink = AudiobookshelfSink(str(library))
        result = await sink.deliver(str(src), BookMetadata())

        assert result.success is True
        assert (library / "Unknown Author" / "My Audiobook" / "My Audiobook.m4b").exists()

    async def test_no_library_path_fails(self):
        sink = AudiobookshelfSink("")
        result = await sink.deliver("/some/file.m4b", BookMetadata())
        assert result.success is False
        assert "not configured" in result.error

    async def test_missing_file_fails(self, tmp_path):
        sink = AudiobookshelfSink(str(tmp_path))
        result = await sink.deliver("/nope/book.m4b", BookMetadata())
        assert result.success is False
        assert "not found" in result.error

    async def test_sanitizes_directory_names(self, tmp_path):
        src = tmp_path / "book.m4b"
        src.write_bytes(b"content")
        library = tmp_path / "abs-library"

        sink = AudiobookshelfSink(str(library))
        meta = BookMetadata(author='Author: "Special"', title="Book/Title")
        result = await sink.deliver(str(src), meta)

        assert result.success is True
        # Unsafe chars should be replaced.
        subdirs = list(library.rglob("book.m4b"))
        assert len(subdirs) == 1
        assert '"' not in str(subdirs[0])
        assert '/' not in subdirs[0].parent.name
