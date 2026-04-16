"""
Scoring + similarity tests.

The enricher's accept decision is downstream of these functions, so
making sure the boundary cases behave sanely is the easiest way to
prevent subtle match-quality regressions.
"""
from app.metadata.scoring import (
    author_overlap,
    score_match,
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
