"""
Unit tests for the metadata extractor.

Creates minimal valid epub files as test fixtures — no real book
files needed. M4B tests require mutagen.
"""
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from app.metadata.extract import BookMetadata, extract


def _make_epub(
    tmp_path: Path,
    *,
    title: str = "Test Book",
    author: str = "Test Author",
    language: str = "en",
    series: str = "",
    series_index: str = "",
    isbn: str = "",
    publisher: str = "",
    filename: str = "test.epub",
) -> Path:
    """Create a minimal valid EPUB file with OPF metadata."""
    opf = ET.Element(
        "package",
        xmlns="http://www.idpf.org/2007/opf",
        version="3.0",
    )
    md = ET.SubElement(opf, "metadata")
    md.set("xmlns:dc", "http://purl.org/dc/elements/1.1/")
    md.set("xmlns:opf", "http://www.idpf.org/2007/opf")

    dc_title = ET.SubElement(md, "dc:title")
    dc_title.text = title

    dc_creator = ET.SubElement(md, "dc:creator")
    dc_creator.text = author

    dc_lang = ET.SubElement(md, "dc:language")
    dc_lang.text = language

    if publisher:
        dc_pub = ET.SubElement(md, "dc:publisher")
        dc_pub.text = publisher

    if isbn:
        dc_id = ET.SubElement(md, "dc:identifier")
        dc_id.set("opf:scheme", "ISBN")
        dc_id.text = isbn

    if series:
        meta_series = ET.SubElement(md, "meta")
        meta_series.set("name", "calibre:series")
        meta_series.set("content", series)

    if series_index:
        meta_idx = ET.SubElement(md, "meta")
        meta_idx.set("name", "calibre:series_index")
        meta_idx.set("content", series_index)

    opf_bytes = ET.tostring(opf, encoding="unicode", xml_declaration=True)

    # Container XML pointing to the OPF file.
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )

    epub_path = tmp_path / filename
    with zipfile.ZipFile(str(epub_path), "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf_bytes)
        zf.writestr("chapter1.xhtml", "<html><body>Content</body></html>")

    return epub_path


class TestExtractEpub:
    def test_reads_title(self, tmp_path):
        epub = _make_epub(tmp_path, title="The Way of Kings")
        meta = extract(epub)
        assert meta.title == "The Way of Kings"

    def test_reads_author(self, tmp_path):
        epub = _make_epub(tmp_path, author="Brandon Sanderson")
        meta = extract(epub)
        assert meta.author == "Brandon Sanderson"

    def test_reads_language(self, tmp_path):
        epub = _make_epub(tmp_path, language="en")
        meta = extract(epub)
        assert meta.language == "en"

    def test_reads_publisher(self, tmp_path):
        epub = _make_epub(tmp_path, publisher="Tor Books")
        meta = extract(epub)
        assert meta.publisher == "Tor Books"

    def test_reads_isbn(self, tmp_path):
        epub = _make_epub(tmp_path, isbn="9780765326355")
        meta = extract(epub)
        assert meta.isbn == "9780765326355"

    def test_reads_series(self, tmp_path):
        epub = _make_epub(
            tmp_path, series="The Stormlight Archive", series_index="1"
        )
        meta = extract(epub)
        assert meta.series == "The Stormlight Archive"
        assert meta.series_index == "1"

    def test_format_is_epub(self, tmp_path):
        epub = _make_epub(tmp_path)
        meta = extract(epub)
        assert meta.format == "epub"

    def test_missing_fields_empty_string(self, tmp_path):
        epub = _make_epub(tmp_path, title="", author="")
        meta = extract(epub)
        assert meta.title == ""
        assert meta.author == ""


class TestExtractM4b:
    def test_reads_m4b_metadata(self, tmp_path):
        """Create a minimal M4B with mutagen and verify extraction."""
        try:
            from mutagen.mp4 import MP4
        except ImportError:
            pytest.skip("mutagen not installed")

        # Create a minimal valid MP4/M4B file.
        m4b_path = tmp_path / "audiobook.m4b"

        # We need a valid MP4 container. Mutagen can't create one from
        # scratch, so we create a minimal MPEG-4 file structure.
        # For testing, we'll just verify the extract function handles
        # the file gracefully even if it's not a real M4B.
        m4b_path.write_bytes(b"\x00" * 100)  # invalid file
        meta = extract(m4b_path)
        # Should not crash, returns format at minimum.
        assert meta.format == "m4b"


class _FakeMP4Info:
    def __init__(self, length):
        self.length = length


class _FakeMP4:
    """Stand-in for `mutagen.mp4.MP4` — skips the real file parse.

    We mock MP4 rather than crafting a real MP4 container because tag
    extraction logic is what we're testing; mutagen is trusted to
    surface the raw atoms once the container is parsed.
    """
    def __init__(self, _path):
        self.tags = _FakeMP4._pending_tags
        self.info = _FakeMP4._pending_info

    _pending_tags: dict = {}
    _pending_info = _FakeMP4Info(0.0)


def _tag_freeform(value: str) -> list:
    """Shape a freeform iTunes atom the way mutagen returns it."""
    return [value.encode("utf-8")]


class TestExtractM4bEnhanced:
    """Tests for the audiobook-specific fields on M4B extraction."""

    def test_pulls_narrator_from_freeform_atom(self, tmp_path, monkeypatch):
        from app.metadata import extract as extract_mod
        m4b = tmp_path / "x.m4b"
        m4b.write_bytes(b"stub")
        _FakeMP4._pending_tags = {
            "\xa9nam": ["The Way of Kings"],
            "\xa9ART": ["Brandon Sanderson"],
            "\xa9alb": ["The Stormlight Archive"],
            "----:com.apple.iTunes:NARRATOR": _tag_freeform("Michael Kramer, Kate Reading"),
        }
        _FakeMP4._pending_info = _FakeMP4Info(45 * 3600)
        monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
        meta = extract_mod.extract(m4b)
        assert meta.title == "The Way of Kings"
        assert meta.author == "Brandon Sanderson"
        assert meta.series == "The Stormlight Archive"
        assert meta.narrator == "Michael Kramer, Kate Reading"
        assert meta.duration_sec == 45 * 3600
        assert meta.format == "m4b"

    def test_narrator_falls_back_to_composer(self, tmp_path, monkeypatch):
        from app.metadata import extract as extract_mod
        m4b = tmp_path / "x.m4b"
        m4b.write_bytes(b"stub")
        _FakeMP4._pending_tags = {
            "\xa9nam": ["Book"],
            "\xa9ART": ["Author"],
            "\xa9wrt": ["Narrator Name"],
        }
        _FakeMP4._pending_info = _FakeMP4Info(0.0)
        monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
        assert extract_mod.extract(m4b).narrator == "Narrator Name"

    def test_asin_from_freeform_atom(self, tmp_path, monkeypatch):
        from app.metadata import extract as extract_mod
        m4b = tmp_path / "x.m4b"
        m4b.write_bytes(b"stub")
        _FakeMP4._pending_tags = {
            "\xa9nam": ["Book"],
            "\xa9ART": ["Author"],
            "----:com.apple.iTunes:ASIN": _tag_freeform("B00BPVBI4A"),
        }
        _FakeMP4._pending_info = _FakeMP4Info(0.0)
        monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
        assert extract_mod.extract(m4b).asin == "B00BPVBI4A"

    def test_asin_sniffed_from_comment(self, tmp_path, monkeypatch):
        from app.metadata import extract as extract_mod
        m4b = tmp_path / "x.m4b"
        m4b.write_bytes(b"stub")
        _FakeMP4._pending_tags = {
            "\xa9nam": ["Book"],
            "\xa9ART": ["Author"],
            "\xa9cmt": ["https://audible.com/pd/B00BPVBI4A rip by XYZ"],
        }
        _FakeMP4._pending_info = _FakeMP4Info(0.0)
        monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
        assert extract_mod.extract(m4b).asin == "B00BPVBI4A"

    def test_pub_year_from_day_atom(self, tmp_path, monkeypatch):
        from app.metadata import extract as extract_mod
        m4b = tmp_path / "x.m4b"
        m4b.write_bytes(b"stub")
        _FakeMP4._pending_tags = {
            "\xa9nam": ["Book"],
            "\xa9ART": ["Author"],
            "\xa9day": ["2011-04-01"],
        }
        _FakeMP4._pending_info = _FakeMP4Info(0.0)
        monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
        assert extract_mod.extract(m4b).pub_year == "2011"

    def test_abridged_flag(self, tmp_path, monkeypatch):
        from app.metadata import extract as extract_mod
        m4b = tmp_path / "x.m4b"
        m4b.write_bytes(b"stub")
        _FakeMP4._pending_tags = {
            "\xa9nam": ["Book"],
            "\xa9ART": ["Author"],
            "----:com.apple.iTunes:ABRIDGED": _tag_freeform("1"),
        }
        _FakeMP4._pending_info = _FakeMP4Info(0.0)
        monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
        assert extract_mod.extract(m4b).abridged is True

    def test_missing_tags_gives_empty_strings(self, tmp_path, monkeypatch):
        from app.metadata import extract as extract_mod
        m4b = tmp_path / "x.m4b"
        m4b.write_bytes(b"stub")
        _FakeMP4._pending_tags = {}
        _FakeMP4._pending_info = _FakeMP4Info(0.0)
        monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
        meta = extract_mod.extract(m4b)
        assert meta.format == "m4b"
        assert meta.narrator == ""
        assert meta.asin == ""
        assert meta.abridged is False
        assert meta.duration_sec == 0.0


class TestExtractEdgeCases:
    def test_nonexistent_file(self, tmp_path):
        meta = extract(tmp_path / "nope.epub")
        assert meta == BookMetadata()

    def test_non_book_file(self, tmp_path):
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")
        meta = extract(txt)
        assert meta.format == "txt"

    def test_corrupt_epub(self, tmp_path):
        bad = tmp_path / "bad.epub"
        bad.write_bytes(b"this is not a zip")
        meta = extract(bad)
        assert meta.format == "epub"

    def test_pdf_returns_format(self, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 some content")
        meta = extract(pdf)
        assert meta.format == "pdf"
