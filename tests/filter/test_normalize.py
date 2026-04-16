"""
Unit tests for the canonical normalization layer.

These rules are load-bearing: every comparison between an announce
author and the allow/ignore lists, every weekly Calibre audit, every
auto-train write goes through `normalize_author`. Bugs here would
cause silent mismatches across the whole pipeline. The tests below
pin the behavior down — anything that breaks them needs to be a
deliberate decision, not an accident.
"""
from app.filter.normalize import extract_format, normalize_author, normalize_category


class TestNormalizeAuthor:
    def test_empty_string(self):
        assert normalize_author("") == ""

    def test_none_safe(self):
        # The function shouldn't crash on falsy input.
        assert normalize_author(None) == ""  # type: ignore[arg-type]

    def test_lowercases(self):
        assert normalize_author("Brandon Sanderson") == "brandon sanderson"

    def test_strips_whitespace(self):
        assert normalize_author("  Brandon Sanderson  ") == "brandon sanderson"

    def test_collapses_internal_whitespace(self):
        assert normalize_author("Brandon    Sanderson") == "brandon sanderson"

    def test_replaces_underscores_with_spaces(self):
        assert normalize_author("Brandon_Sanderson") == "brandon sanderson"

    def test_replaces_hyphens_with_spaces(self):
        assert normalize_author("Brandon-Sanderson") == "brandon sanderson"

    def test_replaces_dots_with_spaces(self):
        # Common in initial-bearing names like "J.R.R. Tolkien"
        assert normalize_author("J.R.R. Tolkien") == "j r r tolkien"

    def test_preserves_apostrophes(self):
        # "O'Brien" must stay distinct from "O Brien"
        assert normalize_author("Patrick O'Brien") == "patrick o'brien"

    def test_typographic_apostrophe_normalized_to_ascii(self):
        # MAM and Calibre both freely emit U+2019 (’) instead of U+0027 (').
        # Real example seen in our autobrr.log:
        #   "I Won’t Let Mistress Suck My Blood, Vol. 1"
        # Without normalization the curly form would be stripped entirely
        # and would never match an author list entry written with the
        # straight form (or vice versa).
        assert normalize_author("Patrick O\u2019Brien") == "patrick o'brien"
        assert normalize_author("Patrick O\u2018Brien") == "patrick o'brien"
        # Both apostrophe forms must produce the SAME canonical output —
        # the test that actually matters for the matching layer.
        assert normalize_author("Patrick O\u2019Brien") == normalize_author(
            "Patrick O'Brien"
        )

    def test_strips_other_punctuation(self):
        assert normalize_author("Brandon Sanderson!") == "brandon sanderson"
        assert normalize_author("(Brandon Sanderson)") == "brandon sanderson"

    def test_calibre_sort_swap_basic(self):
        # Calibre stores "Lastname, Firstname"; MAM uses "Firstname Lastname".
        # The normalize step must reconcile them.
        assert normalize_author("Sanderson, Brandon") == "brandon sanderson"

    def test_calibre_sort_swap_with_initials(self):
        assert normalize_author("Tolkien, J.R.R.") == "j r r tolkien"

    def test_calibre_sort_swap_with_apostrophe(self):
        assert normalize_author("O'Brien, Patrick") == "patrick o'brien"

    def test_multi_comma_no_swap(self):
        # Strings with more than one comma are author lists, not sort
        # names. The normalize layer leaves them alone — split happens
        # before normalize in the real pipeline. We just confirm there's
        # no accidental swap (the words come out in their original order).
        # Commas are preserved in the canonical form (matches the
        # original ebook_gate.sh behavior — the keep-charset includes
        # the comma), which is fine because the gate splits on comma
        # BEFORE calling normalize, so single-author normalized strings
        # never actually contain commas in practice.
        assert normalize_author("Chaney, Anspach, Cole") == "chaney, anspach, cole"

    def test_idempotent(self):
        # Running normalize twice should yield the same result as once.
        once = normalize_author("J.R.R. Tolkien")
        twice = normalize_author(once)
        assert once == twice


class TestNormalizeCategory:
    def test_basic_lowercase(self):
        assert normalize_category("Ebooks - Fantasy") == "ebooks fantasy"

    def test_collapses_separators(self):
        assert normalize_category("Ebooks_-_Fantasy") == "ebooks fantasy"

    def test_audiobooks(self):
        assert normalize_category("AudioBooks - Mystery") == "audiobooks mystery"

    def test_strips_parens(self):
        # MAM IRC announces wrap categories in `( ... )` and the
        # extractor sometimes leaves a stray paren. Normalize must strip.
        assert normalize_category("( Ebooks - Fantasy )") == "ebooks fantasy"

    def test_no_calibre_swap(self):
        # Categories should NOT trigger the Lastname,Firstname swap
        # even if they happen to contain a single comma. The comma
        # is preserved in the canonical form (matches author normalize),
        # but the word order stays as written — that's the property
        # this test pins down.
        assert normalize_category("Ebooks, Fantasy") == "ebooks, fantasy"


class TestExtractFormat:
    def test_ebooks(self):
        assert extract_format("Ebooks - Fantasy") == "ebooks"

    def test_audiobooks(self):
        assert extract_format("AudioBooks - Mystery") == "audiobooks"

    def test_comics(self):
        assert extract_format("Comics/Graphic novels - Fantasy") == "comics graphic novels"

    def test_no_separator(self):
        assert extract_format("Ebooks") == ""

    def test_empty(self):
        assert extract_format("") == ""

    def test_case_normalized(self):
        assert extract_format("EBOOKS - FANTASY") == "ebooks"

    def test_multiple_dashes(self):
        # Only splits on the first " - ".
        assert extract_format("Ebooks - Science - Fiction") == "ebooks"
