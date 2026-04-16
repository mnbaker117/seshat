"""
Similarity scoring for metadata matches.

Ported from Hermeece's battle-tested scoring.py (2026-04-13). Used by
the MAM torrent matcher and the merge pipeline for confidence-based
dedup decisions.

Three title-matching signals combined (best wins):
  - Jaccard overlap — token intersection/union, good for word reordering
  - Containment — fraction of search tokens in result, rewards supersets
  - Substring bonus — verbatim match inside longer string, scaled by coverage

Author overlap: normalize both sides, check set intersection.

Final confidence: 70% title + 30% author + optional series boost.
"""
import re


# ─── Token extraction ───────────────────────────────────────

_WORD_RX = re.compile(r"[a-z0-9']+")

_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "in", "on", "for", "to", "by",
})

# Noise patterns stripped before scoring — series decorations, edition
# info, and "Book N" suffixes that Amazon/Kobo append to titles.
_NOISE_PATTERNS = [
    re.compile(r"\b(?:book|volume|vol|tome|part|episode)\s*#?\d+(?:\.\d+)?\b", re.I),
    re.compile(r"\b(?:kindle|paperback|hardcover|large print|unabridged)\s*edition\b", re.I),
    re.compile(r"\b(?:a novel|a memoir|a thriller)\b", re.I),
]


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
    cleaned = re.sub(r"[\s:—–-]+$", "", cleaned).strip()
    return cleaned


# ─── Author normalization ───────────────────────────────────

# Typographic apostrophe variants → ASCII
_TYPO_APOSTROPHES = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u2032": "'",
})

_KEEP_RX = re.compile(r"[^a-z0-9 ',&]")
_PUNCT_TO_SPACE_RX = re.compile(r"[._\-/]")
_WS_RX = re.compile(r"\s+")

# Author separator pattern for splitting multi-author blobs
_AUTHOR_SEP_RX = re.compile(
    r"(?:\s+and\s+|\s+&\s+|\s*/\s*|\s*;\s*|\s*,\s*)",
    re.IGNORECASE,
)


def normalize_author(name: str) -> str:
    """Canonical form for author-name comparisons.

    Handles "Lastname, Firstname" → "firstname lastname" swap,
    typographic apostrophes, and punctuation normalization.
    """
    if not name:
        return ""
    n = name.strip()
    n = n.translate(_TYPO_APOSTROPHES)
    # Calibre sort-name swap: "Lastname, Firstname" → "Firstname Lastname"
    if n.count(",") == 1:
        last, first = n.split(",", 1)
        last, first = last.strip(), first.strip()
        if last and first:
            n = f"{first} {last}"
    n = n.lower()
    n = _PUNCT_TO_SPACE_RX.sub(" ", n)
    n = _KEEP_RX.sub("", n)
    n = _WS_RX.sub(" ", n).strip()
    return n


def split_authors(blob: str) -> list[str]:
    """Split a multi-author blob into individual author names."""
    if not blob:
        return []
    parts = _AUTHOR_SEP_RX.split(blob)
    return [p.strip() for p in parts if p and p.strip()]


def _normalize_author_set(value) -> set[str]:
    """Normalize author list or string into a comparable set."""
    if isinstance(value, str):
        raw = split_authors(value)
    else:
        raw = value or []
    return {normalize_author(a) for a in raw if normalize_author(a)}


# ─── Scoring functions ──────────────────────────────────────

def title_similarity(a: str, b: str) -> float:
    """Combined title similarity: Jaccard + containment + substring.

    Returns the best score from multiple signals. Both titles are
    cleaned (series/edition noise stripped) before tokenizing.
    """
    if not a or not b:
        return 0.0

    ca = _clean_title(a)
    cb = _clean_title(b)
    ta = _title_tokens(ca)
    tb = _title_tokens(cb)

    if not ta or not tb:
        return 0.0

    inter = len(ta & tb)
    union = len(ta | tb)

    jaccard = inter / union if union else 0.0

    # Containment: what fraction of the search tokens are in the result?
    # Only when both sides have 2+ meaningful tokens.
    containment = 0.0
    if len(ta) >= 2 and len(tb) >= 2:
        containment_a = inter / len(ta) if ta else 0.0
        containment_b = inter / len(tb) if tb else 0.0
        containment = max(containment_a, containment_b)

    # Substring bonus: verbatim match scaled by coverage proportion.
    a_low = a.lower().strip()
    b_low = b.lower().strip()
    substring = 0.0
    if a_low and b_low:
        shorter, longer = (a_low, b_low) if len(a_low) <= len(b_low) else (b_low, a_low)
        if shorter in longer:
            coverage = len(shorter) / len(longer) if longer else 0
            substring = 0.50 + coverage * 0.45

    best = max(jaccard, containment * 0.95, substring)
    return min(best, 1.0)


def author_overlap(candidates, targets) -> float:
    """Fraction of target authors matched by candidates.

    Returns 1.0 when every target matches, 0.0 when none do.
    Anchored on target side so a result with extra authors still
    scores high if it contains the one we're looking for.
    """
    cand = _normalize_author_set(candidates)
    tgt = _normalize_author_set(targets)
    if not tgt:
        return 0.0
    hits = sum(1 for t in tgt if t in cand)
    return hits / len(tgt)


def score_match(
    *,
    record_title: str,
    record_authors: list[str],
    search_title: str,
    search_authors: list[str] | str,
    known_series: str = "",
) -> float:
    """Weighted confidence in [0, 1].

    Title similarity gets 70% weight; author overlap gets 30%.

    If `known_series` is provided, the series name is stripped from the
    record title before scoring AND a 10% boost is applied if the series
    name was found (proves the result is about the same series).
    """
    effective_record = record_title
    series_boost = 0.0

    if known_series and record_title:
        series_lower = known_series.lower().strip()
        record_lower = record_title.lower()
        if series_lower in record_lower:
            series_boost = 0.10
            effective_record = re.sub(
                re.escape(known_series), "", record_title, flags=re.IGNORECASE
            ).strip()
            effective_record = re.sub(r"[\s:—–-]+$", "", effective_record).strip()
            effective_record = re.sub(r"^[\s:—–-]+", "", effective_record).strip()

    ts = title_similarity(effective_record, search_title)
    au = author_overlap(record_authors, search_authors)
    raw = 0.7 * ts + 0.3 * au + series_boost
    return min(raw, 1.0)
