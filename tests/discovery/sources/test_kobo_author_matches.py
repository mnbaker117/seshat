"""
Tests for the v2.10.6 Kobo author-match fix.

Pre-v2.10.6 the matcher in `get_author_books` failed on the very
common case where Kobo's canonical spelling drops spaces between
initials but the queried name keeps them — "J. N. Chaney" (queried)
vs "J.N. Chaney" (Kobo). Period-strip alone gives "j n chaney" vs
"jn chaney" which don't compare equal, so every Chaney book got
silently dropped from Kobo scans.

Same root cause we fixed in HardcoverSource at v2.10.5; the v2.10.6
Kobo fix adds a punctuation+whitespace-insensitive tier between
period-strip and parts-set matching.
"""
from __future__ import annotations

from app.discovery.sources.kobo import _kobo_author_matches


class TestKoboAuthorMatches:
    def test_exact_match(self):
        assert _kobo_author_matches("Brandon Sanderson", "Brandon Sanderson", []) is True

    def test_case_insensitive(self):
        assert _kobo_author_matches("BRANDON SANDERSON", "brandon sanderson", []) is True

    def test_period_strip_initials(self):
        # "J. K. Rowling" queried, Kobo says "JK Rowling" → match via
        # the period-strip tier.
        assert _kobo_author_matches("JK Rowling", "J. K. Rowling", []) is True
        assert _kobo_author_matches("J.K. Rowling", "J K Rowling", []) is True

    def test_v2_10_6_punctuation_whitespace_strip_chaney_regression(self):
        # The v2.10.6 regression: "J. N. Chaney" queried, Kobo
        # says "J.N. Chaney" (no space). Pre-fix: this returned
        # False because period-strip gave "j n chaney" vs "jn chaney".
        # Post-fix: the third tier (strip punct + whitespace)
        # collapses both to "jnchaney".
        assert _kobo_author_matches("J.N. Chaney", "J. N. Chaney", []) is True

    def test_punctuation_whitespace_strip_works_both_directions(self):
        # And the reverse — queried with no spaces, Kobo with spaces.
        assert _kobo_author_matches("J. N. Chaney", "J.N. Chaney", []) is True

    def test_word_order_shuffle(self):
        # "Rowling J K" (some search results / catalog listings
        # display surname-first) should still match "J. K. Rowling".
        assert _kobo_author_matches("Rowling J K", "J. K. Rowling", []) is True

    def test_pen_name_alias_accepted(self):
        # Linked author names (pen names + co-authors) — provided to
        # the matcher as the third arg — should be accepted as if
        # they were the queried name.
        assert _kobo_author_matches(
            "Andrew Karevik",  # the pen name Sanderson... wait no, made-up example
            "Some Pseudonym",
            ["Andrew Karevik", "A. K."],
        ) is True

    def test_unrelated_author_rejected(self):
        assert _kobo_author_matches(
            "Some Other Person", "J. N. Chaney", [],
        ) is False

    def test_empty_card_author_rejected(self):
        assert _kobo_author_matches("", "J. N. Chaney", []) is False

    def test_whitespace_only_card_author_rejected(self):
        assert _kobo_author_matches("   ", "J. N. Chaney", []) is False

    def test_partial_match_rejected(self):
        # "Brandon" alone shouldn't pass for "Brandon Sanderson" —
        # the v2.10.6 collapse is whitespace-INsensitive but still
        # requires the whole identity match.
        assert _kobo_author_matches("Brandon", "Brandon Sanderson", []) is False
        # "Sanderson" alone same deal
        assert _kobo_author_matches("Sanderson", "Brandon Sanderson", []) is False

    def test_co_author_with_target_in_string_rejected(self):
        # "John Smith and Brandon Sanderson" should NOT match a
        # query for "Brandon Sanderson" — would need a per-author
        # split which is the caller's responsibility (see
        # `get_author_books` which iterates `card_authors` one at a
        # time). The matcher's contract is single-name-vs-single-name.
        assert _kobo_author_matches(
            "John Smith and Brandon Sanderson",
            "Brandon Sanderson",
            [],
        ) is False
