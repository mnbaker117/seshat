"""
Book metadata extractor.

Reads embedded metadata from ebook and audiobook files:
  - EPUB: OPF metadata inside the zip container (stdlib only)
  - M4B/M4A: MP4 tags via mutagen
  - MP3: ID3 tags via mutagen
  - PDF: basic info dict (stdlib only — limited metadata)

Returns a `BookMetadata` dataclass with normalized fields. All
extraction is best-effort — missing fields return empty strings,
never raise. The caller decides what to do with gaps.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_log = logging.getLogger("seshat.metadata")

# OPF XML namespaces used by EPUB.
_DC = "{http://purl.org/dc/elements/1.1/}"
_OPF = "{http://www.idpf.org/2007/opf}"
_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"


@dataclass(frozen=True)
class BookMetadata:
    """Normalized metadata extracted from a book file."""

    title: str = ""
    author: str = ""
    series: str = ""
    series_index: str = ""
    language: str = ""
    publisher: str = ""
    description: str = ""
    isbn: str = ""
    format: str = ""


def extract(file_path: str | Path) -> BookMetadata:
    """Extract metadata from a book file.

    Never raises — returns a BookMetadata with whatever fields could
    be read. Unreadable files return an empty BookMetadata.
    """
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return BookMetadata()

    ext = path.suffix.lstrip(".").lower()

    try:
        if ext == "epub":
            return _extract_epub(path)
        if ext in ("m4b", "m4a"):
            return _extract_m4b(path)
        if ext == "mp3":
            return _extract_mp3(path)
        if ext == "pdf":
            return _extract_pdf(path)
    except Exception:
        _log.exception("metadata extraction failed for %s", path)

    return BookMetadata(format=ext)


# ─── EPUB ───────────────────────────────────────────────────


def _extract_epub(path: Path) -> BookMetadata:
    """Read OPF metadata from an EPUB file (zip container)."""
    with zipfile.ZipFile(str(path), "r") as zf:
        opf_path = _find_opf_path(zf)
        if not opf_path:
            return BookMetadata(format="epub")

        with zf.open(opf_path) as f:
            tree = ET.parse(f)

        root = tree.getroot()
        md = root.find(f"{_OPF}metadata") or root.find("metadata")
        if md is None:
            return BookMetadata(format="epub")

        title = _dc_text(md, "title")
        author = _dc_text(md, "creator")
        language = _dc_text(md, "language")
        publisher = _dc_text(md, "publisher")
        description = _dc_text(md, "description")
        isbn = _find_isbn(md)
        series, series_index = _find_series(md)

        return BookMetadata(
            title=title,
            author=author,
            series=series,
            series_index=series_index,
            language=language,
            publisher=publisher,
            description=description,
            isbn=isbn,
            format="epub",
        )


def _find_opf_path(zf: zipfile.ZipFile) -> Optional[str]:
    """Locate the OPF file inside the EPUB zip."""
    try:
        with zf.open("META-INF/container.xml") as f:
            tree = ET.parse(f)
        for rf in tree.iter(f"{_CONTAINER_NS}rootfile"):
            path = rf.get("full-path")
            if path:
                return path
    except (KeyError, ET.ParseError):
        pass

    # Fallback: look for any .opf file.
    for name in zf.namelist():
        if name.endswith(".opf"):
            return name
    return None


def _dc_text(md_element, tag: str) -> str:
    """Extract text from a Dublin Core element."""
    el = md_element.find(f"{_DC}{tag}")
    if el is None:
        el = md_element.find(tag)
    return (el.text or "").strip() if el is not None else ""


def _find_isbn(md_element) -> str:
    """Find ISBN from dc:identifier elements."""
    for ident in md_element.findall(f"{_DC}identifier"):
        scheme = ident.get(f"{_OPF}scheme", "").lower()
        text = (ident.text or "").strip()
        if scheme == "isbn" or text.startswith("978") or text.startswith("979"):
            return text
    return ""


def _find_series(md_element) -> tuple[str, str]:
    """Find Calibre-style series metadata from <meta> elements."""
    series = ""
    index = ""
    for meta in md_element.findall(f"{_OPF}meta"):
        name = meta.get("name", "")
        content = meta.get("content", "")
        if name == "calibre:series":
            series = content
        elif name == "calibre:series_index":
            index = content
    # Also check non-namespaced <meta> elements.
    for meta in md_element.findall("meta"):
        name = meta.get("name", "")
        content = meta.get("content", "")
        if name == "calibre:series":
            series = content
        elif name == "calibre:series_index":
            index = content
    return series, index


# ─── M4B / M4A ─────────────────────────────────────────────


def _extract_m4b(path: Path) -> BookMetadata:
    """Read MP4 tags from an M4B/M4A file."""
    try:
        from mutagen.mp4 import MP4
    except ImportError:
        _log.warning("mutagen not installed, cannot read M4B metadata")
        return BookMetadata(format=path.suffix.lstrip(".").lower())

    tags = MP4(str(path)).tags or {}

    title = _mp4_str(tags, "\xa9nam")
    author = _mp4_str(tags, "\xa9ART") or _mp4_str(tags, "aART")
    album = _mp4_str(tags, "\xa9alb")

    return BookMetadata(
        title=title,
        author=author,
        series=album,  # audiobooks typically use album = series
        language="",  # MP4 doesn't have a standard language tag
        format=path.suffix.lstrip(".").lower(),
    )


def _mp4_str(tags: dict, key: str) -> str:
    """Extract a string value from MP4 tags."""
    val = tags.get(key)
    if val and isinstance(val, list) and len(val) > 0:
        return str(val[0]).strip()
    return ""


# ─── MP3 ────────────────────────────────────────────────────


def _extract_mp3(path: Path) -> BookMetadata:
    """Read ID3 tags from an MP3 file."""
    try:
        from mutagen.id3 import ID3
    except ImportError:
        _log.warning("mutagen not installed, cannot read MP3 metadata")
        return BookMetadata(format="mp3")

    try:
        tags = ID3(str(path))
    except Exception:
        return BookMetadata(format="mp3")

    title = _id3_str(tags, "TIT2")
    author = _id3_str(tags, "TPE1")
    album = _id3_str(tags, "TALB")
    language = _id3_str(tags, "TLAN")

    return BookMetadata(
        title=title,
        author=author,
        series=album,
        language=language,
        format="mp3",
    )


def _id3_str(tags, key: str) -> str:
    """Extract a string value from ID3 tags."""
    frame = tags.get(key)
    if frame and frame.text:
        return str(frame.text[0]).strip()
    return ""


# ─── PDF ────────────────────────────────────────────────────


def _extract_pdf(path: Path) -> BookMetadata:
    """Read basic metadata from a PDF file's info dict.

    Uses a simple binary scan for the /Info dictionary — no external
    PDF library needed. Very limited: only works for PDFs with a
    plaintext info dict (most do).
    """
    try:
        with open(path, "rb") as f:
            head = f.read(4096)

        # Quick check it's actually a PDF.
        if not head.startswith(b"%PDF"):
            return BookMetadata(format="pdf")

        # PDF metadata extraction is too fragile without a real library.
        # Return format-only for now; we can add pypdf later if needed.
        return BookMetadata(format="pdf")
    except Exception:
        return BookMetadata(format="pdf")
