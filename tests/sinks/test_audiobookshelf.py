"""
Unit tests for the Audiobookshelf sink.
"""
from pathlib import Path

import httpx

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


class TestAudiobookshelfSinkScanTrigger:
    """Tests for the post-drop ABS library-scan API call."""

    def _inject_transport(self, monkeypatch, handler):
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kw: orig(
                transport=httpx.MockTransport(handler),
                **{k: v for k, v in kw.items() if k != "transport"},
            ),
        )

    async def test_scan_fires_when_fully_configured(self, tmp_path, monkeypatch):
        scan_calls: list = []

        def handler(request):
            assert request.method == "POST"
            assert request.headers.get("Authorization") == "Bearer test-token"
            scan_calls.append(request.url.path)
            return httpx.Response(200, json={})

        self._inject_transport(monkeypatch, handler)

        src = tmp_path / "book.m4b"
        src.write_bytes(b"content")
        library = tmp_path / "abs-library"

        sink = AudiobookshelfSink(
            str(library),
            abs_base_url="http://abs:13378",
            abs_api_key="test-token",
            abs_library_id="lib-xyz",
        )
        result = await sink.deliver(
            str(src), BookMetadata(author="A", title="B"),
        )
        assert result.success is True
        assert scan_calls == ["/api/libraries/lib-xyz/scan"]

    async def test_scan_skipped_when_api_not_configured(self, tmp_path, monkeypatch):
        """Drop still succeeds when ABS API config is missing."""
        calls: list = []

        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={})

        self._inject_transport(monkeypatch, handler)

        src = tmp_path / "book.m4b"
        src.write_bytes(b"content")
        sink = AudiobookshelfSink(str(tmp_path / "abs-library"))
        result = await sink.deliver(str(src), BookMetadata(author="A", title="B"))
        assert result.success is True
        assert calls == []

    async def test_scan_failure_doesnt_fail_delivery(self, tmp_path, monkeypatch):
        """Network hiccup on scan POST is logged but delivery is still success."""
        def handler(request):
            raise httpx.ConnectError("abs down", request=request)

        self._inject_transport(monkeypatch, handler)

        src = tmp_path / "book.m4b"
        src.write_bytes(b"content")
        sink = AudiobookshelfSink(
            str(tmp_path / "abs-library"),
            abs_base_url="http://abs:13378",
            abs_api_key="test-token",
            abs_library_id="lib-xyz",
        )
        result = await sink.deliver(str(src), BookMetadata(author="A", title="B"))
        assert result.success is True

    async def test_scan_not_fired_when_copy_fails(self, tmp_path, monkeypatch):
        """If the copy step fails, we must not POST a scan request."""
        calls: list = []

        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={})

        self._inject_transport(monkeypatch, handler)

        sink = AudiobookshelfSink(
            str(tmp_path / "abs-library"),
            abs_base_url="http://abs:13378",
            abs_api_key="test-token",
            abs_library_id="lib-xyz",
        )
        # Missing source file → deliver() returns failure before scan.
        result = await sink.deliver("/nope/book.m4b", BookMetadata())
        assert result.success is False
        assert calls == []
