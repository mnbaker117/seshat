"""Normalize source descriptions (and similar long-form text) to plain
text.

MAM uploaders paste a mix of BBCode, raw HTML, and HTML entities into
the synopsis box; Google Books descriptions are HTML; Goodreads /
Hardcover / Kobo can leak inline tags. Without normalization the
review queue surfaces literal `<p>...</p>` and `&#8212;` to the user.

`description_to_plain_text` is idempotent on already-clean strings, so
sources can call it unconditionally instead of trying to detect which
encoding they got.
"""

from __future__ import annotations

import html
import re
from typing import Optional


# All quantifiers below are bounded (no naked `*` / `+` on character
# classes) — CodeQL flagged the unbounded variants as
# polynomial-ReDoS surfaces because description text comes from
# third-party sources (Amazon, OpenLibrary, Google Books, etc.).
# `{0,200}` on the BBCode attribute value is well over any
# legitimate attr (`[size=14]`, `[color=#ff0000]`). `{1,1000}` on
# the HTML tag interior covers anchor tags with long URLs without
# permitting unbounded backtracking on adversarial `<<<<…` runs.
_BBCODE_TAG = re.compile(
    r"\[/?(?:b|i|u|s|size|color|url|img|quote|code|spoiler|list|\*)"
    r"(?:=[^\]]{0,200})?\]",
    re.IGNORECASE,
)
_BBCODE_HR = re.compile(r"\[hr\]", re.IGNORECASE)
# Dropped the leading `\s*` after `<` and the trailing `\s*` before
# `>` because two adjacent `\s*` quantifiers can match the same
# whitespace run in two ways, which triggers quadratic backtracking
# on adversarial `<br ` (no closing) inputs.
_HTML_BR = re.compile(r"<br\s{0,10}/?>", re.IGNORECASE)
_HTML_BLOCK_CLOSE = re.compile(r"</\s{0,10}(?:p|div|li)\s{0,10}>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]{1,1000}>")
_MULTI_NL = re.compile(r"\n{3,}")

# Defensive cap. No legitimate book description in any of the
# scraped sources runs longer than a few KB; the cap is two orders
# of magnitude above that. Adversarial multi-MB inputs from a
# misbehaving source can't pin a worker thread.
_MAX_INPUT_CHARS = 50_000


def description_to_plain_text(text: Optional[str]) -> Optional[str]:
    """Strip BBCode + HTML and decode entities. Returns None on empty."""
    if not text:
        return None
    if len(text) > _MAX_INPUT_CHARS:
        text = text[:_MAX_INPUT_CHARS]
    # BBCode (MAM legacy)
    text = _BBCODE_TAG.sub("", text)
    text = _BBCODE_HR.sub("\n", text)
    # HTML block boundaries → newlines before stripping all tags so
    # paragraphs don't collapse into a single run-on line.
    text = _HTML_BR.sub("\n", text)
    text = _HTML_BLOCK_CLOSE.sub("\n\n", text)
    text = _HTML_TAG.sub("", text)
    # Entities (&#8212; → em-dash, &amp; → &, etc.).
    text = html.unescape(text)
    # Whitespace
    text = text.replace("\r\n", "\n")
    text = _MULTI_NL.sub("\n\n", text)
    cleaned = text.strip()
    return cleaned or None
