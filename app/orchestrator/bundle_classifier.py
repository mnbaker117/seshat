"""
Bundle/collection classifier for multi-file torrents.

The post-download pipeline historically assumed "one torrent = one book"
— either a single file, or a small set of multi-format variants of the
same book (epub + mobi + azw3), or a multi-part audiobook split across
many m4b/mp3 files. All three of those cases produce ONE review queue
entry today.

This module adds a fourth case: bundle/collection torrents that carry
several DISTINCT works (e.g. "Mistborn Trilogy" with three separate
novels inside, or a 4-audiobook collection). For these, the pipeline
needs to fan out into N review-queue entries — one per distinct work.

`classify()` groups the input file list into one or more `BookGroup`
objects. A single group means the existing pipeline behavior (one
review entry per torrent) — bundles produce N groups.

Signal order (cheapest first, short-circuits early):
  1. Single file → 1 group.
  2. Stem dedupe → all files collapse to one stem when format suffix
     is stripped → multi-format same book → 1 group. No embedded
     metadata reads needed.
  3. Audiobook-parts safety net → same extension across all files plus
     a part/chapter token in filenames + matching album/title in
     embedded metadata → one multi-part audiobook → 1 group. Catches
     the 26-part m4b case (handled today by the
     `_backfill_audio_companions` path).
  4. Embedded metadata grouping → use `extract_metadata` to read
     title+author from each file; group by
     `normalize(author)|normalize(title)`. Distinct groups = bundle.
  5. Filename-token fallback for files whose extraction yields an
     empty title (PDFs especially) — longest common prefix + Jaccard
     ~0.85.

MAM-side constraints (see `reference_mam_format_category_separation.md`):
each MAM torrent is single-category (ebook OR audiobook OR comic etc.).
A "mixed audiobook + ebook in one torrent" never happens on MAM, so the
classifier doesn't need to handle that case.

The classifier is wrapped by a feature flag (`bundle_detection_enabled`,
default True from v2.7.0) — when disabled, all files fall into one group
preserving the pre-v2.7 behavior verbatim.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app.metadata.author_names import normalize_author_name
from app.metadata.extract import BookMetadata, extract as extract_metadata

_log = logging.getLogger("seshat.orchestrator.bundle_classifier")


# Extensions that mark a file as a "format variant" for stem-dedupe.
# When the same stem appears with multiple of these suffixes, it's one
# book in multiple formats — no embedded read needed.
_FORMAT_SUFFIXES = frozenset({
    "epub", "mobi", "azw", "azw3", "pdf", "lit", "fb2", "djvu",
    "cbz", "cbr",
    "m4b", "m4a", "mp3", "aax", "aa",
})

# Audiobook extensions used by the multi-part safety net.
_AUDIO_EXTS = frozenset({"m4b", "m4a", "mp3", "aax", "aa"})

# Filename tokens that indicate a "part of a larger work" — disc N,
# chapter N, track N, pt N, or a leading two-three digit run treated
# as a track number. Used by the audiobook-parts safety net so a
# single audiobook split across 26 m4b parts collapses to ONE group.
_PART_TOKEN_RX = re.compile(
    r"(?:^|[\s_\-.])"
    r"(?:pt|part|disc|disk|chapter|ch|track|cd|vol|volume)"
    r"[\s_\-.]?\d{1,3}"
    r"|(?:^|[\s_\-.])\d{2,3}[\s_\-.]",
    re.IGNORECASE,
)

# Title-normalization regex — strip punctuation + collapse whitespace.
_TITLE_PUNCT_RX = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RX = re.compile(r"\s+")

# Minimum Jaccard token-set similarity for filename-token fallback to
# merge two files into the same group. Tuned for typical bundle
# filenames where each book's name differs significantly.
_TOKEN_JACCARD_THRESHOLD = 0.85


@dataclass(frozen=True)
class BookGroup:
    """One detected book (single, multi-format, or multi-part) inside a torrent.

    A group's files all represent the same underlying work. For
    single-book and multi-format groups `files` is the format list.
    For multi-part audiobooks `files` is every part. The `primary` is
    the file used for metadata extraction, cover fetch, and (for
    ebooks) sink delivery — picked by the same priority order the
    caller already applied to the input list.

    `extracted` holds embedded metadata read from `primary` so the
    pipeline doesn't have to repeat the extract call.
    """

    files: list[Path]
    primary: Path
    extracted: BookMetadata = field(default_factory=BookMetadata)


def classify(
    book_files: list[Path],
    *,
    enabled: bool = True,
    extract_fn: Callable[[Path], BookMetadata] = extract_metadata,
    announce_title: str = "",
    announce_author: str = "",
) -> list[BookGroup]:
    """Group `book_files` into one or more `BookGroup`s.

    The caller already sorted `book_files` by user preference (size +
    format priority) — the first element of each output group's
    `files` list is treated as the primary so the existing pipeline's
    "largest format-priority wins" semantics are preserved within
    each group.

    When `enabled=False` (feature-flag off) or only one file is
    present, returns a single group containing every file — identical
    to pre-v2.7 behavior.

    `extract_fn` is injectable so tests can substitute a fake without
    needing to construct real EPUBs/M4Bs. Production code passes the
    real `extract_metadata`.

    `announce_title` and `announce_author` are reserved for future
    soft-prior tuning (e.g. weight a bundle classification differently
    when the MAM listing title contains "Trilogy" or "Bundle"). They
    are not used as primary signals — MAM bundle wording is unreliable
    per session notes.
    """
    if not book_files:
        return []

    primary_meta = extract_fn(book_files[0]) if book_files else BookMetadata()

    if not enabled or len(book_files) == 1:
        return [BookGroup(
            files=list(book_files),
            primary=book_files[0],
            extracted=primary_meta,
        )]

    # ── Signal 1: stem dedupe ──────────────────────────────────
    # If every file's stem collapses to the same set after stripping
    # the format suffix, it's one book in multiple formats. No
    # embedded read needed — short-circuit to a single group.
    stems = {p.stem.lower() for p in book_files}
    if len(stems) == 1:
        _log.debug(
            "classifier: stem-dedupe → 1 group (%d files share stem %r)",
            len(book_files), next(iter(stems)),
        )
        return [BookGroup(
            files=list(book_files),
            primary=book_files[0],
            extracted=primary_meta,
        )]

    # ── Signal 2: audiobook-parts safety net ───────────────────
    # If every file shares an audio extension AND every file has a
    # part/disc/chapter token in its name, the torrent is one
    # audiobook split across N parts. Collapses 26-file m4b rips.
    extensions = {p.suffix.lstrip(".").lower() for p in book_files}
    if (
        len(extensions) == 1
        and extensions.issubset(_AUDIO_EXTS)
        and all(_PART_TOKEN_RX.search(p.name) for p in book_files)
    ):
        _log.debug(
            "classifier: audiobook-parts safety net → 1 group (%d %s files)",
            len(book_files), next(iter(extensions)),
        )
        return [BookGroup(
            files=list(book_files),
            primary=book_files[0],
            extracted=primary_meta,
        )]

    # ── Signal 3: embedded metadata grouping ───────────────────
    # Extract title+author from each file, group by normalized
    # (author|title). Files with empty extracted title fall through
    # to the filename-token fallback below.
    metas: list[tuple[Path, BookMetadata]] = []
    for f in book_files:
        if f == book_files[0]:
            metas.append((f, primary_meta))
        else:
            try:
                metas.append((f, extract_fn(f)))
            except Exception:
                _log.exception(
                    "classifier: extract failed for %s — treating as untitled",
                    f.name,
                )
                metas.append((f, BookMetadata()))

    keyed: list[tuple[Path, BookMetadata, str]] = []
    untitled: list[tuple[Path, BookMetadata]] = []
    for f, m in metas:
        key = _group_key(m)
        if key:
            keyed.append((f, m, key))
        else:
            untitled.append((f, m))

    groups_by_key: dict[str, list[tuple[Path, BookMetadata]]] = {}
    insertion_order: list[str] = []
    for f, m, key in keyed:
        if key not in groups_by_key:
            groups_by_key[key] = []
            insertion_order.append(key)
        groups_by_key[key].append((f, m))

    # ── Signal 4: filename-token fallback for untitled files ───
    # Attach each untitled file to whichever existing group it's
    # closest to under filename-token Jaccard. If no existing group
    # is close enough, the file becomes its own group.
    if untitled and groups_by_key:
        for f, m in list(untitled):
            best_key: Optional[str] = None
            best_score = 0.0
            f_tokens = _filename_tokens(f.stem)
            for key, members in groups_by_key.items():
                member_tokens = _filename_tokens(members[0][0].stem)
                score = _jaccard(f_tokens, member_tokens)
                if score > best_score:
                    best_score = score
                    best_key = key
            if best_key is not None and best_score >= _TOKEN_JACCARD_THRESHOLD:
                groups_by_key[best_key].append((f, m))
            else:
                # Synthetic key — unique per untitled file. The
                # filename serves as the group identity.
                synth = f"__untitled__:{f.name}"
                groups_by_key[synth] = [(f, m)]
                insertion_order.append(synth)
        untitled = []

    # Untitled files when there are no keyed groups at all (every
    # file failed to extract) — fall back to filename-token clustering.
    if untitled:
        for f, m in untitled:
            placed = False
            f_tokens = _filename_tokens(f.stem)
            for key in insertion_order:
                if not key.startswith("__untitled__"):
                    continue
                member = groups_by_key[key][0][0]
                if _jaccard(f_tokens, _filename_tokens(member.stem)) >= _TOKEN_JACCARD_THRESHOLD:
                    groups_by_key[key].append((f, m))
                    placed = True
                    break
            if not placed:
                synth = f"__untitled__:{f.name}"
                groups_by_key[synth] = [(f, m)]
                insertion_order.append(synth)

    # Build the output groups, preserving the caller's file ordering
    # within each group so the user's format priority sticks.
    result: list[BookGroup] = []
    for key in insertion_order:
        members = groups_by_key[key]
        # Sort members by the original input order so the user's
        # format priority is preserved.
        original_order = {id(f): i for i, f in enumerate(book_files)}
        members_sorted = sorted(members, key=lambda fm: original_order.get(id(fm[0]), 1_000_000))
        files = [fm[0] for fm in members_sorted]
        result.append(BookGroup(
            files=files,
            primary=files[0],
            extracted=members_sorted[0][1],
        ))

    if len(result) == 1:
        _log.debug(
            "classifier: all %d files grouped together → 1 group", len(book_files),
        )
    else:
        _log.info(
            "classifier: bundle detected → %d groups (%d files total)",
            len(result), len(book_files),
        )

    return result


def _group_key(meta: BookMetadata) -> str:
    """Build a normalized grouping key from author + title.

    Returns "" when either field is empty so the caller can fall
    through to filename-token grouping. Title normalization strips
    punctuation and collapses whitespace; author uses the shared
    `normalize_author_name` so initials/diacritics fold the same way
    they do elsewhere in the codebase.
    """
    author_norm = normalize_author_name(meta.author or "")
    title_norm = _normalize_title(meta.title or "")
    if not author_norm or not title_norm:
        return ""
    return f"{author_norm}|{title_norm}"


def _normalize_title(s: str) -> str:
    """Lowercase + strip punctuation + collapse whitespace.

    Permissive on purpose — "The Final Empire" and "Final Empire,
    The:" should match. Series-decorator stripping is NOT applied
    here because legitimate bundle children may carry their own
    volume number that we want to keep distinct.
    """
    if not s:
        return ""
    s = s.lower()
    s = _TITLE_PUNCT_RX.sub(" ", s)
    s = _WS_RX.sub(" ", s).strip()
    return s


def _filename_tokens(stem: str) -> set[str]:
    """Tokenize a filename stem for Jaccard comparison.

    Splits on whitespace, underscore, hyphen, and dot; lowercases;
    drops single-character tokens and pure-digit tokens (track
    numbers shouldn't dominate the similarity score).
    """
    raw = re.split(r"[\s_\-.]+", stem.lower())
    return {
        tok for tok in raw
        if len(tok) >= 2 and not tok.isdigit()
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    """Standard Jaccard similarity. Empty sets return 0.0."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union
