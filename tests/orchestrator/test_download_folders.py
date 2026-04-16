"""
Unit tests for monthly download folder management.
"""
from datetime import datetime, timezone
from pathlib import Path

from app.orchestrator.download_folders import (
    current_month_folder,
    ensure_folder_exists,
    translate_path,
)


class TestCurrentMonthFolder:
    def test_basic_path(self):
        dt = datetime(2026, 4, 10, tzinfo=timezone.utc)
        result = current_month_folder("/downloads/[mam-complete]", now=dt)
        assert result == "/downloads/[mam-complete]/[2026-04]"

    def test_january(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = current_month_folder("/downloads/[mam-complete]", now=dt)
        assert result == "/downloads/[mam-complete]/[2026-01]"

    def test_december(self):
        dt = datetime(2026, 12, 31, tzinfo=timezone.utc)
        result = current_month_folder("/downloads/[mam-complete]", now=dt)
        assert result == "/downloads/[mam-complete]/[2026-12]"

    def test_empty_base_returns_empty(self):
        assert current_month_folder("") == ""

    def test_trailing_slash_handled(self):
        dt = datetime(2026, 4, 10, tzinfo=timezone.utc)
        result = current_month_folder("/downloads/[mam-complete]/", now=dt)
        assert result == "/downloads/[mam-complete]/[2026-04]"

    def test_uses_current_time_by_default(self):
        # Just verify it doesn't crash without a `now` argument.
        result = current_month_folder("/downloads/test")
        assert "[20" in result  # sanity check for year prefix


class TestEnsureFolderExists:
    def test_creates_folder(self, tmp_path):
        target = str(tmp_path / "[2026-04]")
        assert ensure_folder_exists(target) is True
        assert Path(target).is_dir()

    def test_existing_folder_ok(self, tmp_path):
        target = tmp_path / "[2026-04]"
        target.mkdir()
        assert ensure_folder_exists(str(target)) is True

    def test_nested_creation(self, tmp_path):
        target = str(tmp_path / "deep" / "nested" / "[2026-04]")
        assert ensure_folder_exists(target) is True
        assert Path(target).is_dir()

    def test_empty_path_returns_false(self):
        assert ensure_folder_exists("") is False


class TestTranslatePath:
    def test_qbit_to_local(self):
        result = translate_path("/data/[mam-complete]/book", "/data", "/downloads")
        assert result == "/downloads/[mam-complete]/book"

    def test_local_to_qbit(self):
        result = translate_path("/downloads/[mam-complete]/book", "/downloads", "/data")
        assert result == "/data/[mam-complete]/book"

    def test_no_match_returns_unchanged(self):
        result = translate_path("/other/path/book", "/data", "/downloads")
        assert result == "/other/path/book"

    def test_exact_prefix_match(self):
        result = translate_path("/data", "/data", "/downloads")
        assert result == "/downloads"

    def test_trailing_slashes_handled(self):
        result = translate_path("/data/[mam-complete]", "/data/", "/downloads/")
        assert result == "/downloads/[mam-complete]"

    def test_empty_path(self):
        assert translate_path("", "/data", "/downloads") == ""

    def test_empty_prefix(self):
        assert translate_path("/data/book", "", "/downloads") == "/data/book"
