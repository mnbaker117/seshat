"""
Canonical normalization for author names and category strings.

This is the single source of truth for how names get compared throughout
Seshat — the filter, the weekly Calibre audit, the auto-train flow,
and the dedupe logic in the author lists all flow through here.

The rules are a deliberate port of the `normalize()` shell function in
`previous-stuff/ebook_gate.sh`, with one extension: we also handle the
"Lastname, Firstname" → "firstname lastname" reordering that Calibre
uses but MAM does not. Without that swap, the weekly Calibre audit would
fail to recognize that Calibre's "Sanderson, Brandon" and MAM's "Brandon
Sanderson" refer to the same author.

Rules (applied in order):
  1. Strip leading/trailing whitespace.
  2. Convert typographic apostrophes (`’` U+2019, `‘` U+2018) to the
     ASCII apostrophe (`'`). Both MAM and Calibre data freely mix the
     two forms — without this step "I Won't" and "I Won’t" would
     normalize to different strings and silently fail to match.
  3. If the string contains exactly one comma, treat it as a
     "Lastname, Firstname" Calibre-style sort name and swap the halves.
     (Multi-comma strings are author lists and should be split before
     calling normalize.)
  4. Lowercase.
  5. Replace `_`, `-`, `.` with a single space.
  6. Strip every character that isn't alphanumeric, space, apostrophe,
     comma, or ampersand. (Apostrophe is preserved for "O'Brien" etc.;
     comma and ampersand are preserved as a safety net even though we
     prefer split-then-normalize.)
  7. Collapse runs of whitespace and trim.

Categories follow the same pipeline but skip the comma-swap step
(categories never have personal-name word order).
"""
import re

# Anything outside this charset gets stripped to a space.
_KEEP_RX = re.compile(r"[^a-z0-9 ',&]")
_WS_RX = re.compile(r"\s+")
_PUNCT_TO_SPACE_RX = re.compile(r"[._\-/]")

# Typographic apostrophe / single-quote variants seen in real MAM and
# Calibre data. All collapse to the ASCII apostrophe before the keep
# pass so they survive normalization instead of being stripped.
_TYPO_APOSTROPHES = str.maketrans({
    "\u2018": "'",  # ‘ left single quotation mark
    "\u2019": "'",  # ’ right single quotation mark
    "\u02bc": "'",  # ʼ modifier letter apostrophe
    "\u2032": "'",  # ′ prime
})


def _swap_calibre_sort(name: str) -> str:
    """If `name` looks like 'Lastname, Firstname', swap to 'Firstname Lastname'.

    Only swaps when there's exactly one comma — multi-comma strings are
    almost always author lists and should be split before reaching this
    function. Returns the input unchanged if no swap applies.
    """
    if name.count(",") != 1:
        return name
    last, first = name.split(",", 1)
    last = last.strip()
    first = first.strip()
    if not last or not first:
        return name
    return f"{first} {last}"


def normalize_author(name: str) -> str:
    """Canonical form for author-name comparisons."""
    if not name:
        return ""
    n = name.strip()
    n = n.translate(_TYPO_APOSTROPHES)
    n = _swap_calibre_sort(n)
    n = n.lower()
    n = _PUNCT_TO_SPACE_RX.sub(" ", n)
    n = _KEEP_RX.sub("", n)
    n = _WS_RX.sub(" ", n).strip()
    return n


def extract_format(category: str) -> str:
    """Extract and normalize the format prefix from a MAM category string.

    MAM categories follow the pattern "Format - Subcategory":
      "Ebooks - Fantasy"                → "ebooks"
      "AudioBooks - Mystery"            → "audiobooks"
      "Comics/Graphic novels - Fantasy" → "comics graphic novels"

    Returns empty string if the category has no " - " separator.
    """
    if not category or " - " not in category:
        return ""
    prefix = category.split(" - ", 1)[0]
    return normalize_category(prefix)


def normalize_category(category: str) -> str:
    """Canonical form for MAM category strings.

    Same pipeline as `normalize_author` minus the Calibre name swap,
    with one addition: intra-word hyphens are stripped (not replaced
    with space) so "E-Books" → "ebooks" matches the IRC announce
    form "Ebooks". Hyphens surrounded by spaces (" - ") are replaced
    with a single space to keep "Ebooks - Fantasy" → "ebooks fantasy".
    """
    if not category:
        return ""
    c = category.strip().translate(_TYPO_APOSTROPHES).lower()
    # Replace " - " separators with space first (before stripping hyphens).
    c = c.replace(" - ", " ")
    # Strip remaining intra-word hyphens: "e-books" → "ebooks".
    c = c.replace("-", "")
    c = _PUNCT_TO_SPACE_RX.sub(" ", c)
    c = _KEEP_RX.sub("", c)
    c = _WS_RX.sub(" ", c).strip()
    return c
