"""
Scoring + similarity tests.

The enricher's accept decision is downstream of these functions, so
making sure the boundary cases behave sanely is the easiest way to
prevent subtle match-quality regressions.
"""
from app.metadata.scoring import (
    _extract_volume,
    author_overlap,
    score_match,
    score_match_with_breakdown,
    title_similarity,
)


class TestTitleSimilarity:
    def test_exact_match_is_one(self):
        assert title_similarity("Foundation", "Foundation") == 1.0

    def test_word_order_invariant(self):
        assert title_similarity("Kings Way", "Way Kings") == 1.0

    def test_partial_overlap(self):
        # "Foundation" vs "Foundation and Empire" → one title is a
        # substring of the other. Since a09d063 the scoring weights
        # containment more heavily, producing ~0.71 (was <0.6 under
        # the old pure-token-overlap formula). The higher score is
        # correct behavior: a single-word title matching the first
        # word of a multi-word title IS a strong signal.
        score = title_similarity("Foundation", "Foundation and Empire")
        assert 0.6 < score < 0.8

    def test_disjoint_is_zero(self):
        assert title_similarity("Mistborn", "Dune") == 0.0

    def test_empty_inputs(self):
        assert title_similarity("", "Foundation") == 0.0
        assert title_similarity("Foundation", "") == 0.0

    def test_stopwords_dropped(self):
        # "The Way of Kings" vs "Way Kings" → all content tokens match.
        assert title_similarity("The Way of Kings", "Way Kings") == 1.0


class TestAuthorOverlap:
    def test_full_match_list(self):
        assert author_overlap(["Brandon Sanderson"], ["Brandon Sanderson"]) == 1.0

    def test_blob_vs_blob(self):
        assert author_overlap(
            "Brandon Sanderson, Janci Patterson",
            "Janci Patterson",
        ) == 1.0

    def test_no_overlap(self):
        assert author_overlap(["Isaac Asimov"], ["Frank Herbert"]) == 0.0

    def test_empty_target_is_zero(self):
        assert author_overlap(["Someone"], []) == 0.0

    def test_case_insensitive(self):
        assert author_overlap(
            ["brandon sanderson"], ["Brandon Sanderson"]
        ) == 1.0


class TestScoreMatch:
    def test_perfect_match_is_high(self):
        score = score_match(
            record_title="The Way of Kings",
            record_authors=["Brandon Sanderson"],
            search_title="The Way of Kings",
            search_authors="Brandon Sanderson",
        )
        assert score >= 0.95

    def test_title_only_match_is_lower(self):
        score = score_match(
            record_title="The Way of Kings",
            record_authors=["Someone Else"],
            search_title="The Way of Kings",
            search_authors="Brandon Sanderson",
        )
        # 0.7 from title, 0 from authors → 0.7
        assert 0.65 < score < 0.75

    def test_author_only_match_is_lowest(self):
        score = score_match(
            record_title="Mistborn",
            record_authors=["Brandon Sanderson"],
            search_title="The Way of Kings",
            search_authors="Brandon Sanderson",
        )
        # 0 from title, 0.3 from authors → 0.3
        assert 0.25 < score < 0.35


class TestExtractVolume:
    def test_book_n(self):
        assert _extract_volume("Foo: Book 5") == 5

    def test_volume_n(self):
        assert _extract_volume("Foo, Volume 12") == 12

    def test_vol_with_period(self):
        assert _extract_volume("Foo Vol. 3") == 3

    def test_no_volume(self):
        assert _extract_volume("Foundation") is None

    def test_bare_range_does_not_match(self):
        # "1-4" lacks the keyword prefix — bundle territory, handled by
        # Part B, not the volume-mismatch guard.
        assert _extract_volume("The Demon Accords 1-4") is None

    def test_empty(self):
        assert _extract_volume("") is None


class TestSeriesStripFallback:
    """Regression tests for the empty-residue fallback path.

    When the series-strip + clean removes everything that would
    distinguish this record from a sibling volume, the old code
    scored ts=0 and the result landed at 0.40 — a 'Possible' badge
    on a 100%-correct URL. The fix falls back to comparing original
    titles, with a volume-mismatch guard to keep Book 2 from being
    promoted as a match for Book 5.
    """

    def test_self_titled_series_first_book_promotes(self):
        # 1-800-Starship by J. N. Chaney — series == title == record.
        # Old behavior: confidence=0.40 → "Possible".
        b = score_match_with_breakdown(
            record_title="1-800-STARSHIP",
            record_authors=["J N Chaney"],
            search_title="1-800-Starship",
            search_authors="J. N. Chaney",
            known_series="1-800-Starship",
        )
        assert b["fallback_to_full_title"] is True
        assert b["confidence"] >= 0.95

    def test_series_name_prefix_with_calibre_subtitle_promotes(self):
        # Calibre adds a subtitle MAM doesn't have. Bikini Days case.
        b = score_match_with_breakdown(
            record_title="Bikini Days",
            record_authors=["Michael Dalton"],
            search_title="Bikini Days: An Unconventional Romance",
            search_authors="Michael Dalton",
            known_series="Bikini Days",
        )
        assert b["fallback_to_full_title"] is True
        assert b["confidence"] >= 0.95

    def test_book_n_residue_promotes_when_volumes_match(self):
        # Strip leaves "Book 5", which _clean_title eats via the
        # volume-noise pattern → empty residue. Volumes match → promote.
        b = score_match_with_breakdown(
            record_title="Blackwood Milk Farm: Book 5",
            record_authors=["Eden Redd"],
            search_title="Blackwood Milk Farm: Book 5",
            search_authors="Eden Redd",
            known_series="Blackwood Milk Farm",
        )
        assert b["fallback_to_full_title"] is True
        assert b["confidence"] >= 0.95

    def test_volume_mismatch_returns_zero(self):
        # Same series, different volumes — definitively wrong book.
        # Without this guard, the empty-residue fallback would score
        # Book 2 just as high as Book 5 (clean_title erases the
        # volume from both, ts=1.0).
        b = score_match_with_breakdown(
            record_title="Blackwood Milk Farm: Book 2",
            record_authors=["Eden Redd"],
            search_title="Blackwood Milk Farm: Book 5",
            search_authors="Eden Redd",
            known_series="Blackwood Milk Farm",
        )
        assert b["confidence"] == 0.0
        assert b.get("volume_mismatch") is True

    def test_bundle_range_residue_does_not_falsely_promote(self):
        # "The Demon Accords 1-4" — strip → "1-4" → all-numeric residue.
        # No volume extractable from "1-4" (no keyword), so the guard
        # doesn't fire. Falls back to full title comparison, which
        # against "Duel Nature" still scores ts=0 because the tokens
        # don't overlap. Confidence stays low — Part B handles bundles.
        b = score_match_with_breakdown(
            record_title="The Demon Accords 1-4",
            record_authors=["John Conroe"],
            search_title="Duel Nature",
            search_authors="John Conroe",
            known_series="The Demon Accords",
        )
        assert b["confidence"] < 0.5

    def test_normal_strip_still_works(self):
        # Strip leaves real tokens — current behavior unchanged.
        # "The Triangulum Fold: The Fold Series Book 8" with series
        # "The Fold" → strip → "The Triangulum Fold: Series Book 8"
        # which has plenty of non-numeric tokens.
        b = score_match_with_breakdown(
            record_title="The Triangulum Fold: The Fold Series Book 8",
            record_authors=["A Author"],
            search_title="The Triangulum Fold",
            search_authors="A Author",
            known_series="The Fold",
        )
        assert b["fallback_to_full_title"] is False
        assert b["series_stripped"] is True
        # Strong title overlap + author + series boost → high score.
        assert b["confidence"] >= 0.85

    def test_no_series_no_fallback(self):
        # When known_series is empty, none of this logic runs.
        b = score_match_with_breakdown(
            record_title="Foundation",
            record_authors=["Isaac Asimov"],
            search_title="Foundation",
            search_authors="Isaac Asimov",
        )
        assert b["fallback_to_full_title"] is False
        assert b["series_stripped"] is False
        assert b["confidence"] >= 0.95
