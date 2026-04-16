"""
Author / category filter — the gate that decides whether a MAM
announce should be grabbed, skipped, or queued for review.

The filter is a pure function: it takes an `Announce` plus a
`FilterConfig` and returns a `Decision`. No file I/O, no database
writes, no logging side effects. Persistence is the caller's job.

This is a Python port of the original `previous-stuff/ebook_gate.sh`
shell script. The decision matrix and normalization rules are kept
intentionally faithful so behavior is identical to the existing
production filter on day one.
"""
from app.filter.gate import (
    Announce,
    Decision,
    FilterConfig,
    evaluate_announce,
    extract_author_blob_from_text,
    split_authors,
)
from app.filter.normalize import normalize_author, normalize_category

__all__ = [
    "Announce",
    "Decision",
    "FilterConfig",
    "evaluate_announce",
    "extract_author_blob_from_text",
    "split_authors",
    "normalize_author",
    "normalize_category",
]
