"""
Similarity scoring for metadata matches.

When a source returns a book, we need to decide: is THIS what we were
searching for, or a different book that happened to share some words?
The enricher calls `score_match()` with the search criteria and the
returned record; the score is used to accept, reject, or fall through
to the next source.

Three title-matching signals combined:

  - **Jaccard overlap** — token intersection / union over normalized
    title words. Good at catching same-words-different-order.
  - **Containment** — fraction of search tokens found in the result.
    Scores high when the result is a superset of the search (e.g.
    "The Triangulum Fold" inside "The Triangulum Fold: The Fold
    Series Book 8"). Pure Jaccard punishes this case; containment
    rewards it.
  - **Substring bonus** — if the search title appears verbatim in the
    result (case-insensitive), apply a floor of 0.85. This catches
    cases like exact title + series decoration.

Author overlap: normalize both sides, check set intersection.

Final confidence is a weighted blend of the best title signal (max of
Jaccard and containment) with author overlap.
"""
from __future__ import annotations

import re

from app.filter.gate import split_authors
from app.filter.normalize import normalize_author

_WORD_RX = re.compile(r"[a-z0-9']+")

# Noise patterns stripped before scoring — series decorations, edition
# info, and "Book N" suffixes that Amazon/Kobo append to titles.
_NOISE_PATTERNS = [
    re.compile(r"\b(?:book|volume|vol|tome|part|episode)\s*#?\d+(?:\.\d+)?\b", re.I),
    re.compile(r"\b(?:kindle|paperback|hardcover|large print|unabridged)\s*edition\b", re.I),
    re.compile(r"\b(?:a novel|a memoir|a thriller)\b", re.I),
]

_STOPWORDS = frozenset({"the", "a", "an", "of", "and", "in", "on", "for", "to", "by"})


def _title_tokens(title: str) -> set[str]:
    """Normalize a title into a set of comparison tokens."""
    lowered = title.lower() if title else ""
    tokens = set(_WORD_RX.findall(lowered))
    tokens -= _STOPWORDS
    return tokens


def _clean_title(title: str) -> str:
    """Strip series/edition noise from a title before scoring."""
    cleaned = title
    for pat in _NOISE_PATTERNS:
        cleaned = pat.sub("", cleaned)
    # Strip trailing colons, dashes, and whitespace left by removal.
    cleaned = re.sub(r"[\s:—–-]+$", "", cleaned).strip()
    return cleaned


def title_similarity(a: str, b: str) -> float:
    """Combined title similarity using Jaccard + containment + substring.

    Returns the best score from multiple signals:
      - Jaccard: good for same-words-different-order
      - Containment: good when result is superset of search
      - Substring: catches verbatim title inside longer strings

    Both titles are cleaned (series/edition noise stripped) before
    tokenizing so "The Triangulum Fold" vs "The Triangulum Fold:
    The Fold Series Book 8" compares as cleanly as possible.
    """
    if not a or not b:
        return 0.0

    # First try with cleaned titles (noise stripped).
    ca = _clean_title(a)
    cb = _clean_title(b)
    ta = _title_tokens(ca)
    tb = _title_tokens(cb)

    if not ta or not tb:
        return 0.0

    inter = len(ta & tb)
    union = len(ta | tb)

    jaccard = inter / union if union else 0.0

    # Containment: what fraction of the SEARCH tokens are in the result?
    # This rewards cases where the search is fully contained in the result.
    # Only use containment when the search has 2+ meaningful tokens —
    # single-word titles trivially get 100% containment against anything.
    containment = 0.0
    if len(ta) >= 2 and len(tb) >= 2:
        containment_a = inter / len(ta) if ta else 0.0  # how much of A is in B
        containment_b = inter / len(tb) if tb else 0.0  # how much of B is in A
        containment = max(containment_a, containment_b)

    # Substring bonus: if one title appears verbatim in the other
    # (case-insensitive), boost the score — but scale by how much of
    # the longer string the shorter one covers. "Foundation" inside
    # "Foundation and Empire" covers ~45% → lower bonus. "The
    # Triangulum Fold" inside "The Triangulum Fold: Book 8" covers
    # ~60% → higher bonus.
    a_low = a.lower().strip()
    b_low = b.lower().strip()
    substring = 0.0
    if a_low and b_low:
        shorter, longer = (a_low, b_low) if len(a_low) <= len(b_low) else (b_low, a_low)
        if shorter in longer:
            coverage = len(shorter) / len(longer) if longer else 0
            # Scale: 50% coverage → 0.70, 75% → 0.85, 100% → 0.95
            substring = 0.50 + coverage * 0.45

    # Take the best signal. Weight containment slightly below substring
    # since full containment with noise is still very strong.
    best = max(jaccard, containment * 0.95, substring)
    return min(best, 1.0)


def author_overlap(
    candidates: list[str] | str, targets: list[str] | str
) -> float:
    """Fraction of target authors matched by candidates.

    Both sides accept either a pre-split list or a raw blob string.
    Normalization goes through `normalize_author` so typographic
    variants and "Lastname, Firstname" forms compare equal.

    Returns the fraction of TARGET authors that appear in the
    candidate set — 1.0 when every target matches, 0.5 when half
    match, 0.0 when none do. Anchoring on the target side means
    an announce with one author still scores high even if the
    scraped record lists three.
    """
    cand = _normalize_set(candidates)
    tgt = _normalize_set(targets)
    if not tgt:
        return 0.0
    hits = sum(1 for t in tgt if t in cand)
    return hits / len(tgt)


def _normalize_set(value: list[str] | str) -> set[str]:
    if isinstance(value, str):
        raw = split_authors(value)
    else:
        raw = value
    out: set[str] = set()
    for entry in raw:
        norm = normalize_author(entry)
        if norm:
            out.add(norm)
    return out


def score_match(
    *,
    record_title: str,
    record_authors: list[str],
    search_title: str,
    search_authors: list[str] | str,
    known_series: str = "",
) -> float:
    """Weighted confidence in [0, 1].

    Title similarity (best of Jaccard/containment/substring) gets 70%
    weight; author overlap gets 30%.

    If `known_series` is provided (e.g. from MAM), the series name is
    stripped from the record title before scoring AND a boost is applied
    if the series name was found (proves the result is about the same
    series, not a coincidental title match).
    """
    effective_record = record_title
    series_boost = 0.0

    if known_series and record_title:
        # Strip the series name from the result title for cleaner scoring.
        # Amazon: "The Triangulum Fold: The Fold Series Book 8"
        # MAM series: "The Fold" → strip → "The Triangulum Fold: Series Book 8"
        series_lower = known_series.lower().strip()
        record_lower = record_title.lower()
        if series_lower in record_lower:
            # Series name found in result — boost confidence and strip it.
            series_boost = 0.10
            # Remove the series name and clean up leftover punctuation.
            effective_record = re.sub(
                re.escape(known_series), "", record_title, flags=re.IGNORECASE
            ).strip()
            effective_record = re.sub(r"[\s:—–-]+$", "", effective_record).strip()
            effective_record = re.sub(r"^[\s:—–-]+", "", effective_record).strip()

    ts = title_similarity(effective_record, search_title)
    au = author_overlap(record_authors, search_authors)
    raw = 0.7 * ts + 0.3 * au + series_boost
    return min(raw, 1.0)
