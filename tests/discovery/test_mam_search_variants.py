"""Search-cascade alternate-form generator tests.

`_alternate_title_forms` and `_alternate_author_forms` generate
variant queries for the cascade's passes 6+. They exist to bridge
MAM's stricter tokenization (no space-collapsing on initials,
zero-padding sensitivity on trailing numbers) so the search returns
the right candidate when our source-side form differs from MAM's.
"""
import pytest

from app.discovery.sources.mam import (
    _alternate_author_forms,
    _alternate_title_forms,
    _build_variant_pass_list,
    _clean_title,
    _clean_title_loose,
)


# ─── Title variants ─────────────────────────────────────────────


class TestAlternateTitleForms:
    @pytest.mark.parametrize("title,expected", [
        # Canonical D6/Warhawk case — trailing number stripped
        ("Right of Retribution 2", ["Right of Retribution"]),
        ("Domestic Decay 2", ["Domestic Decay"]),
        ("School of Magic 2", ["School of Magic"]),
        ("Past Life Hero 2", ["Past Life Hero"]),
        # Multi-digit trailing number
        ("My Series 12", ["My Series"]),
        # Single trailing number with extra whitespace
        ("Foo  3  ", ["Foo"]),
    ])
    def test_strips_trailing_number(self, title, expected):
        assert _alternate_title_forms(title) == expected

    @pytest.mark.parametrize("title", [
        # No trailing number
        "The Way of Kings",
        "Foundation",
        # Only whitespace + number guards against false positives — the
        # regex requires a space before the digit, so these don't match
        # — they're intentionally not stripped:
        "Foundation1",
        # Mid-title number — anchored to end so this doesn't match
        "Apollo 11 Mission Report",
        # Empty / falsy
        "",
        "   ",
        # Result of stripping would be too short
        "AB 5",
    ])
    def test_negative_no_strip(self, title):
        assert _alternate_title_forms(title) == []


# ─── Author variants ────────────────────────────────────────────


class TestAlternateAuthorForms:
    @pytest.mark.parametrize("author,expected", [
        # Canonical Veil case
        ("J J Cross", ["JJ Cross", "J.J. Cross"]),
        # 3-initial author
        ("J R R Tolkien", ["JRR Tolkien", "J.R.R. Tolkien"]),
        # With existing periods
        ("J. K. Rowling", ["JK Rowling", "J.K. Rowling"]),
        ("P. G. Wodehouse", ["PG Wodehouse", "P.G. Wodehouse"]),
        # Concatenated form → split variants
        ("JK Rowling", ["J K Rowling", "J.K. Rowling"]),
        ("JRR Tolkien", ["J R R Tolkien", "J.R.R. Tolkien"]),
    ])
    def test_generates_initial_variants(self, author, expected):
        assert _alternate_author_forms(author) == expected

    @pytest.mark.parametrize("author", [
        # Single author name — no initials
        "Tolkien",
        "Catherine Fisher",
        "Brandon Sanderson",
        # First-name + surname (no initials)
        "Brandon Sanderson",
        # Single initial only — not enough to bridge tokenization
        "J Smith",
        # Empty
        "",
    ])
    def test_no_variants_for_non_initial_authors(self, author):
        assert _alternate_author_forms(author) == []

    def test_excludes_input_form(self):
        # Input "JJ Cross" should not appear in its own variant list.
        out = _alternate_author_forms("JJ Cross")
        assert "JJ Cross" not in out

    def test_dedupes_collapsed_variants(self):
        # If concat and with_periods coincidentally equal, only one
        # appears in the output. (Edge case — single-letter surname.)
        # Using "J K Rowling" as the canonical case where concat
        # ("JK Rowling") and with_periods ("J.K. Rowling") differ.
        out = _alternate_author_forms("J K Rowling")
        assert len(out) == len(set(out))


# ─── Combined cases known from UAT ──────────────────────────────


class TestKnownUatCases:
    """Pin the specific cases that A1+A3 UAT identified as fixable."""

    def test_d6_right_of_retribution(self):
        # Currently-stored URL fails because MAM has "Right of
        # Retribution 02" (zero-padded). Stripping the number
        # surfaces the right tid.
        assert "Right of Retribution" in _alternate_title_forms(
            "Right of Retribution 2"
        )

    def test_veil_jj_cross(self):
        # MAM has "JJ Cross"; Calibre has "J J Cross". Variants must
        # include the no-space form.
        variants = _alternate_author_forms("J J Cross")
        assert "JJ Cross" in variants

    def test_warhawk_amnesty_typographic(self):
        # MAM uploaded "Warhawk’s Amnesty" with U+2019 right single
        # quote; Mark's Calibre uses ASCII apostrophe. The search
        # treats them as distinct tokens — variant must swap.
        variants = _alternate_title_forms("Warhawk's Amnesty")
        assert "Warhawk’s Amnesty" in variants


# ─── Typographic / smart-quote normalization ────────────────────


class TestTypographicVariants:
    def test_ascii_apostrophe_to_typographic(self):
        # Canonical Warhawk case
        assert "Warhawk’s Amnesty" in _alternate_title_forms(
            "Warhawk's Amnesty"
        )

    def test_typographic_apostrophe_to_ascii(self):
        # Reverse direction — when source has the typographic form
        # and MAM has ASCII, swap that direction too.
        assert "Warhawk's Amnesty" in _alternate_title_forms(
            "Warhawk’s Amnesty"
        )

    def test_multiple_apostrophes(self):
        # Both apostrophes should swap to typographic (the swap is
        # global per-pair).
        variants = _alternate_title_forms("Foo's Bar's Baz")
        assert "Foo’s Bar’s Baz" in variants

    def test_typographic_compounds_with_trailing_number(self):
        # Title has BOTH a trailing number AND an apostrophe — should
        # produce the trailing-stripped form, the apostrophe-swapped
        # form, AND the both-stripped form.
        variants = _alternate_title_forms("Foo's Bar 2")
        assert "Foo's Bar" in variants            # trailing stripped
        assert "Foo’s Bar 2" in variants     # apostrophe swapped
        assert "Foo’s Bar" in variants       # both

    def test_double_quotes(self):
        variants = _alternate_title_forms('A "Special" Story')
        # ASCII double quotes → curly variants
        assert any("”" in v or "“" in v for v in variants)

    def test_no_punctuation_no_typographic_variants(self):
        # Plain titles get no typographic variant since they have no
        # punctuation to swap.
        assert _alternate_title_forms("Plain Title") == []


# ─── _clean_title apostrophe preservation (Warhawk fix) ────────


class TestCleanTitlePreservesApostrophes:
    """Pin the 2026-05-09 fix: apostrophes (ASCII + typographic) must
    survive `_clean_title` so MAM's full-text search can tokenize on
    the same word boundaries it uses to index titles. Pre-fix, both
    `'` and `’` were stripped, turning "Warhawk's" into "Warhawks"
    which matched NOTHING in MAM's index. Verified against the live
    Warhawk's Amnesty case during the second UAT round."""

    def test_ascii_apostrophe_preserved(self):
        assert _clean_title("Warhawk's Amnesty") == "Warhawk's Amnesty"

    def test_typographic_apostrophe_preserved(self):
        assert _clean_title("Warhawk’s Amnesty") == "Warhawk’s Amnesty"

    def test_left_single_quote_preserved(self):
        # Less common but symmetric — pin it so a future regex
        # tightening doesn't accidentally strip it.
        assert _clean_title("Foo‘s Bar") == "Foo‘s Bar"

    def test_periods_still_replaced_with_space(self):
        # Mid-word period still gets RE_ADD_SPACE'd (becomes a space)
        # then RE_PUNCT'd (no remaining punct). Apostrophe preservation
        # MUST NOT regress this.
        assert _clean_title("Foo.Bar") == "Foo Bar"

    def test_other_special_chars_still_stripped(self):
        # Non-apostrophe special chars (& # @ etc.) keep getting stripped
        # so the change is narrowly scoped.
        assert _clean_title("Foo & Bar") == "Foo Bar"

    def test_loose_variant_also_preserves_apostrophes(self):
        # The loose form (used for title-only pass 5) preserves hyphens
        # AND must also preserve apostrophes for consistency.
        assert _clean_title_loose("Warhawk's Amnesty") == "Warhawk's Amnesty"
        assert _clean_title_loose("Warhawk’s Amnesty") == "Warhawk’s Amnesty"


# ─── _build_variant_pass_list pairings ──────────────────────────


class TestBuildVariantPassList:
    """Pin the variant-pass list shape so a future refactor can't
    silently drop the (alt_author, short) pairing that the Veil case
    depends on."""

    def test_alt_author_paired_with_short_title(self):
        # Veil canary — alt_authors=['JJ Cross', 'J.J. Cross'],
        # short='The Veil', title='The Veil: A Dark Bio-Punk Sci-Fi
        # Thriller'. The variant list MUST include
        # ('JJ Cross', 'The Veil') because MAM only returns the
        # right tid 1120995 for that specific (alt_author, short)
        # combination — not (alt_author, full_title) which excludes
        # the tid.
        variants = _build_variant_pass_list(
            title="The Veil: A Dark Bio-Punk Sci-Fi Thriller",
            authors="J J Cross",
            core=None,
            sub_right="A Dark Bio-Punk Sci-Fi Thriller",
            short="The Veil",
            title_only="A Dark Bio-Punk Sci-Fi Thriller",
        )
        assert ("JJ Cross", "The Veil") in variants

    def test_alt_title_paired_with_original_author(self):
        # Right of Retribution canary — alt_title='Right of Retribution'
        # (trailing num stripped) paired with the original author.
        variants = _build_variant_pass_list(
            title="Right of Retribution 2",
            authors="William D. Arand",
            core=None,
            sub_right=None,
            short=None,
            title_only="Right of Retribution 2",
        )
        assert ("William D. Arand", "Right of Retribution") in variants

    def test_typographic_alt_with_original_author(self):
        # Warhawk canary — typographic-apostrophe variant of the title
        # paired with original author.
        variants = _build_variant_pass_list(
            title="Warhawk's Amnesty",
            authors="Ajax Lygan",
            core=None,
            sub_right=None,
            short=None,
            title_only="Warhawk's Amnesty",
        )
        assert ("Ajax Lygan", "Warhawk’s Amnesty") in variants

    def test_dedup_against_passes_1_to_5(self):
        # Variants matching passes 1-5 must not appear (would burn
        # API quota on duplicates).
        variants = _build_variant_pass_list(
            title="Foo",
            authors="Bar",
            core=None,
            sub_right=None,
            short=None,
            title_only="Foo",
        )
        for pair in variants:
            assert pair != ("Bar", "Foo")  # would be pass 1
            assert pair != (None, "Foo")    # would be pass 5

    def test_cap_respected(self):
        # Worst case (3 alt_titles × 3 alt_authors + simple combos)
        # should be capped — pin the max length.
        variants = _build_variant_pass_list(
            title="J K Rowling's Magic 2",  # multiple variant axes
            authors="J R R Tolkien",
            core=None,
            sub_right=None,
            short="J K Rowling's Magic",
            title_only="J K Rowling's Magic 2",
            cap=4,
        )
        assert len(variants) <= 4
