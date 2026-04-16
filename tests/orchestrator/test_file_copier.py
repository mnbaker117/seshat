"""
Unit tests for the file copier.

Uses pytest's tmp_path fixture for real filesystem operations.
"""
from pathlib import Path

from app.orchestrator.file_copier import (
    BOOK_EXTENSIONS,
    CopyResult,
    copy_to_staging,
    find_book_files,
)


def _create_file(path: Path, size: int = 100) -> Path:
    """Create a file with the given size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


class TestFindBookFiles:
    def test_finds_epub(self, tmp_path):
        _create_file(tmp_path / "book.epub")
        assert len(find_book_files(tmp_path)) == 1

    def test_finds_m4b(self, tmp_path):
        _create_file(tmp_path / "audiobook.m4b")
        assert len(find_book_files(tmp_path)) == 1

    def test_ignores_nfo(self, tmp_path):
        _create_file(tmp_path / "info.nfo")
        assert len(find_book_files(tmp_path)) == 0

    def test_ignores_jpg(self, tmp_path):
        _create_file(tmp_path / "cover.jpg")
        assert len(find_book_files(tmp_path)) == 0

    def test_sorted_by_size_descending(self, tmp_path):
        _create_file(tmp_path / "small.epub", size=50)
        _create_file(tmp_path / "large.epub", size=500)
        files = find_book_files(tmp_path)
        assert files[0].name == "large.epub"
        assert files[1].name == "small.epub"

    def test_recursive_search(self, tmp_path):
        _create_file(tmp_path / "subdir" / "nested.epub")
        files = find_book_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "nested.epub"

    def test_nonexistent_dir(self, tmp_path):
        assert find_book_files(tmp_path / "nope") == []

    def test_single_file_as_source(self, tmp_path):
        f = _create_file(tmp_path / "book.epub")
        files = find_book_files(f)
        assert len(files) == 1

    def test_single_non_book_file(self, tmp_path):
        f = _create_file(tmp_path / "readme.txt")
        files = find_book_files(f)
        assert len(files) == 0


class TestCopyToStaging:
    def test_copies_epub_to_staging(self, tmp_path):
        source = tmp_path / "downloads" / "My Book"
        staging = tmp_path / "staging"
        _create_file(source / "My Book.epub", size=200)

        result = copy_to_staging(source, staging, "My Book")

        assert result.success is True
        assert result.files_copied == 1
        assert result.book_format == "epub"
        assert result.book_filename == "My Book.epub"
        assert Path(result.staged_path).exists()
        assert (Path(result.staged_path) / "My Book.epub").exists()

    def test_copies_multiple_files(self, tmp_path):
        source = tmp_path / "downloads" / "Series Pack"
        staging = tmp_path / "staging"
        _create_file(source / "book1.epub", size=100)
        _create_file(source / "book2.epub", size=200)
        _create_file(source / "cover.jpg", size=50)  # ignored

        result = copy_to_staging(source, staging, "Series Pack")

        assert result.success is True
        assert result.files_copied == 2
        # Primary file should be the largest.
        assert result.book_filename == "book2.epub"

    def test_no_book_files_fails(self, tmp_path):
        source = tmp_path / "downloads" / "Empty"
        staging = tmp_path / "staging"
        _create_file(source / "readme.txt")

        result = copy_to_staging(source, staging, "Empty")

        assert result.success is False
        assert "no book files" in result.error

    def test_staging_not_configured(self, tmp_path):
        source = tmp_path / "downloads"
        result = copy_to_staging(source, Path(""), "Book")
        assert result.success is False
        assert "not configured" in result.error

    def test_creates_staging_subdir(self, tmp_path):
        source = tmp_path / "dl"
        staging = tmp_path / "staging"
        _create_file(source / "book.epub")

        copy_to_staging(source, staging, "My Great Book")

        assert (staging / "My Great Book").is_dir()

    def test_sanitizes_dirname(self, tmp_path):
        source = tmp_path / "dl"
        staging = tmp_path / "staging"
        _create_file(source / "book.epub")

        copy_to_staging(source, staging, 'Book: A "Title" With <Bad> Chars')

        # Should have replaced unsafe chars.
        subdirs = list(staging.iterdir())
        assert len(subdirs) == 1
        assert ":" not in subdirs[0].name
        assert '"' not in subdirs[0].name

    def test_original_file_preserved(self, tmp_path):
        source = tmp_path / "dl"
        staging = tmp_path / "staging"
        original = _create_file(source / "book.epub", size=300)

        copy_to_staging(source, staging, "Book")

        # Original file must still exist (for seeding).
        assert original.exists()
        assert original.stat().st_size == 300
