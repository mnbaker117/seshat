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
    """Normalized metadata extracted from a book file.

    Fields at the top (title → format) are shared across all book
    formats. Audiobook-specific fields (narrator, duration_sec,
    abridged, asin) stay empty for ebooks and are populated by the
    m4b/mp3 extractors when present in the file's embedded tags.
    """

    title: str = ""
    author: str = ""
    series: str = ""
    series_index: str = ""
    language: str = ""
    publisher: str = ""
    description: str = ""
    isbn: str = ""
    format: str = ""
    # ── Audiobook-specific ──────────────────────────────────
    narrator: str = ""
    asin: str = ""
    pub_year: str = ""
    duration_sec: float = 0.0
    abridged: bool = False


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
    """Read MP4 tags from an M4B/M4A file.

    Audible-ripped M4B files carry a rich tag set — we pull every
    field the pipeline's audiobook enrichment cares about:
      \xa9nam = title
      \xa9ART = artist (author; Audible uses author here)
      aART   = album artist (fallback; also used by some rips)
      \xa9alb = album (treated as series name for audiobooks)
      \xa9wrt = composer (sometimes narrator on Audible rips)
      \xa9nrt / ----:com.apple.iTunes:NARRATOR = explicit narrator tag
      desc / ----:com.apple.iTunes:comment     = description / summary
      \xa9day = release year
      ----:com.apple.iTunes:ASIN                = Audible ASIN
      \xa9cmt = comment (occasionally holds ASIN on older rips)

    Duration comes from the file's MP4 info structure (info.length)
    regardless of whether tags are present.
    """
    try:
        from mutagen.mp4 import MP4
    except ImportError:
        _log.warning("mutagen not installed, cannot read M4B metadata")
        return BookMetadata(format=path.suffix.lstrip(".").lower())

    mp4 = MP4(str(path))
    tags = mp4.tags or {}

    title = _mp4_str(tags, "\xa9nam")
    author = _mp4_str(tags, "\xa9ART") or _mp4_str(tags, "aART")
    album = _mp4_str(tags, "\xa9alb")
    # Narrator candidates, in priority order. Audible Inc. rips use
    # `----:com.apple.iTunes:NARRATOR`; older rippers abuse the
    # composer slot. Any non-empty wins.
    narrator = (
        _mp4_freeform(tags, "NARRATOR")
        or _mp4_str(tags, "\xa9nrt")
        or _mp4_str(tags, "\xa9wrt")
    )
    description = (
        _mp4_str(tags, "desc")
        or _mp4_str(tags, "\xa9cmt")
        or _mp4_freeform(tags, "comment")
    )
    pub_year = _mp4_year(_mp4_str(tags, "\xa9day"))
    # ASIN is stored as freeform iTunes atom on Audible rips. Some
    # older tools stuff it in the comment field — fall back to
    # sniffing an ASIN pattern out of the comment string.
    asin = (
        _mp4_freeform(tags, "ASIN")
        or _mp4_freeform(tags, "CDEK")  # audible internal identifier = ASIN in most cases
        or _sniff_asin(_mp4_str(tags, "\xa9cmt"))
    )
    abridged = _mp4_freeform(tags, "ABRIDGED").lower() in ("1", "true", "yes")

    duration_sec = 0.0
    if mp4.info and getattr(mp4.info, "length", None):
        try:
            duration_sec = float(mp4.info.length)
        except (TypeError, ValueError):
            duration_sec = 0.0

    return BookMetadata(
        title=title,
        author=author,
        series=album,  # audiobooks typically use album = series
        language="",  # MP4 doesn't have a standard language tag
        description=description,
        format=path.suffix.lstrip(".").lower(),
        narrator=narrator,
        asin=asin,
        pub_year=pub_year,
        duration_sec=duration_sec,
        abridged=abridged,
    )


def _mp4_str(tags: dict, key: str) -> str:
    """Extract a string value from MP4 tags."""
    val = tags.get(key)
    if val and isinstance(val, list) and len(val) > 0:
        return str(val[0]).strip()
    return ""


def _mp4_freeform(tags: dict, suffix: str) -> str:
    """Extract a freeform iTunes atom (`----:com.apple.iTunes:X`).

    Mutagen represents these as a list of `MP4FreeForm` byte blobs.
    We decode the first one as UTF-8 and return its stripped string
    value, or "" if the atom isn't present.
    """
    full_key = f"----:com.apple.iTunes:{suffix}"
    val = tags.get(full_key)
    if not val:
        return ""
    first = val[0] if isinstance(val, list) else val
    try:
        if isinstance(first, (bytes, bytearray)):
            return bytes(first).decode("utf-8", errors="replace").strip()
        return str(first).strip()
    except Exception:
        return ""


def _mp4_year(value: str) -> str:
    """Extract a 4-digit year from a date-ish string.

    Audible rips sometimes put ISO dates in `\xa9day` ("2011-04-01");
    others put just the year ("2011"). We only keep the year.
    """
    if not value:
        return ""
    import re as _re
    m = _re.search(r"(\d{4})", value)
    return m.group(1) if m else ""


def _sniff_asin(text: str) -> str:
    """Return an ASIN-shaped token from free text, else ''.

    Amazon ASINs are 10 alphanumeric uppercase characters, always
    starting with B. We anchor on word boundaries so we don't match
    arbitrary 10-char fragments.
    """
    if not text:
        return ""
    import re as _re
    m = _re.search(r"\b(B[0-9A-Z]{9})\b", text)
    return m.group(1) if m else ""


# ─── MP3 ────────────────────────────────────────────────────


def _extract_mp3(path: Path) -> BookMetadata:
    """Read ID3 tags from an MP3 file.

    Audiobook MP3 rips use these ID3 frames:
      TIT2 = title
      TPE1 = lead performer (author for Audible; narrator for some rippers)
      TPE2 = band/album artist (typically author when TPE1 holds narrator)
      TALB = album (series/book name)
      TCOM = composer (narrator fallback)
      TCON = genre (sometimes holds "Audiobook")
      TDRC = recording date (year)
      COMM = comment (description; may hide ASIN)
      USLT = unsynced lyrics (sometimes holds description)
      TXXX:ASIN / TXXX:NARRATOR = user-defined extended frames

    Duration comes from mutagen's info.length (seconds, float).
    """
    try:
        from mutagen.id3 import ID3
        from mutagen.mp3 import MP3
    except ImportError:
        _log.warning("mutagen not installed, cannot read MP3 metadata")
        return BookMetadata(format="mp3")

    try:
        mp3 = MP3(str(path))
    except Exception:
        return BookMetadata(format="mp3")

    tags = mp3.tags if mp3.tags is not None else ID3()

    title = _id3_str(tags, "TIT2")
    # Prefer TPE2 (album artist) for author when present — most
    # audiobook rippers put the narrator in TPE1 and the author in
    # TPE2. Fall back to TPE1 for rips that only use one frame.
    author = _id3_str(tags, "TPE2") or _id3_str(tags, "TPE1")
    album = _id3_str(tags, "TALB")
    language = _id3_str(tags, "TLAN")
    narrator = (
        _id3_txxx(tags, "NARRATOR")
        or _id3_str(tags, "TPE1") if _id3_str(tags, "TPE2") else ""
    ) or _id3_str(tags, "TCOM")
    description = (
        _id3_comm(tags)
        or _id3_uslt(tags)
    )
    pub_year = _mp4_year(_id3_str(tags, "TDRC") or _id3_str(tags, "TYER"))
    asin = _id3_txxx(tags, "ASIN") or _sniff_asin(_id3_comm(tags))

    duration_sec = 0.0
    if mp3.info and getattr(mp3.info, "length", None):
        try:
            duration_sec = float(mp3.info.length)
        except (TypeError, ValueError):
            duration_sec = 0.0

    return BookMetadata(
        title=title,
        author=author,
        series=album,
        language=language,
        description=description,
        format="mp3",
        narrator=narrator,
        asin=asin,
        pub_year=pub_year,
        duration_sec=duration_sec,
    )


def _id3_str(tags, key: str) -> str:
    """Extract a string value from ID3 tags."""
    frame = tags.get(key)
    if frame and getattr(frame, "text", None):
        return str(frame.text[0]).strip()
    return ""


def _id3_txxx(tags, desc: str) -> str:
    """Extract a user-defined TXXX frame by its description field."""
    for frame in tags.getall("TXXX") if hasattr(tags, "getall") else []:
        if getattr(frame, "desc", "").upper() == desc.upper():
            if frame.text:
                return str(frame.text[0]).strip()
    return ""


def _id3_comm(tags) -> str:
    """Extract the first COMM (comment) frame's text."""
    for frame in tags.getall("COMM") if hasattr(tags, "getall") else []:
        if frame.text:
            return str(frame.text[0]).strip()
    return ""


def _id3_uslt(tags) -> str:
    """Extract the first USLT (unsynced lyrics) frame's text."""
    for frame in tags.getall("USLT") if hasattr(tags, "getall") else []:
        if frame.text:
            return str(frame.text).strip()
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
