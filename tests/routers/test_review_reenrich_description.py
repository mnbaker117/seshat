"""Unit tests for `_resolve_reenrich_description`.

Guards the v2.17.4 fix that migrates pre-v2.17.3 review rows with
raw HTML/BBCode descriptions to plain text on re-enrich. The old
longest-wins-only rule kept dirty-longer over clean-shorter, so
existing queue items would never lose their tags without manual
intervention.
"""

from app.routers.review import _resolve_reenrich_description


class TestEnrichedWins:
    def test_enriched_longer_than_clean_current_promotes(self):
        out = _resolve_reenrich_description(
            current="Short existing.",
            enriched="A much longer enriched description from Goodreads.",
        )
        assert out == "A much longer enriched description from Goodreads."

    def test_enriched_longer_than_dirty_current_after_cleaning_promotes(self):
        # Stored row has HTML so its byte-length is inflated. After
        # cleaning, the enriched plain-text is longer.
        dirty = "<p>brief blurb.</p>"  # 19 chars dirty, 12 cleaned
        rich = "A genuinely longer back-of-book synopsis from a real source."
        out = _resolve_reenrich_description(current=dirty, enriched=rich)
        assert out == rich


class TestCleanedCurrentWins:
    def test_dirty_html_current_migrates_to_plain_text(self):
        # The Mortedant's Peril shape: HTML wraps the prose, no
        # re-enrich result available. Helper should still return
        # the cleaned form so the row gets updated.
        dirty = (
            "<p><b>In a city of ancient automata</b>, a cleric of "
            "death finds his own life on the line&#8212;though no "
            "one thanks him.<br>This is the synopsis.</p>"
        )
        out = _resolve_reenrich_description(current=dirty, enriched=None)
        assert "<" not in out
        assert ">" not in out
        assert "&#8212;" not in out
        assert "—" in out
        assert "In a city of ancient automata" in out

    def test_dirty_current_beats_shorter_clean_enriched(self):
        # Pre-v2.17.3 row has 500 chars of HTML; new source-cleaned
        # enriched is only 450 chars of plain text. Pure longest-wins
        # would lose the cleanup. Our promotion rule cleans current
        # before comparing, so equal-or-shorter enriched yields to
        # the cleaned current.
        dirty = "<p>" + ("Long synopsis body. " * 25) + "</p>"  # ~510 raw
        cleaned_len = len(
            "Long synopsis body. " * 25
        )
        shorter_enriched = "Short summary from a different source."
        assert len(shorter_enriched) < cleaned_len
        out = _resolve_reenrich_description(
            current=dirty, enriched=shorter_enriched,
        )
        assert "<p>" not in out
        assert out.startswith("Long synopsis body.")

    def test_bbcode_current_also_gets_cleaned(self):
        dirty = "[b]Bold prelude[/b] then the rest of the synopsis."
        out = _resolve_reenrich_description(current=dirty, enriched=None)
        assert out == "Bold prelude then the rest of the synopsis."


class TestNoMutationNeeded:
    def test_clean_current_no_enriched_returns_none(self):
        # Caller will skip the write — saves a no-op DB update.
        out = _resolve_reenrich_description(
            current="Already plain text.",
            enriched=None,
        )
        assert out is None

    def test_clean_current_shorter_enriched_returns_none(self):
        out = _resolve_reenrich_description(
            current="A reasonably sized existing description here.",
            enriched="Short.",
        )
        assert out is None

    def test_empty_inputs_return_none(self):
        assert _resolve_reenrich_description(current=None, enriched=None) is None
        assert _resolve_reenrich_description(current="", enriched="") is None
        assert _resolve_reenrich_description(current="   ", enriched=None) is None


class TestEnrichedEqualsClean:
    def test_equal_length_does_not_promote_enriched(self):
        # Equal-length tie: don't churn the row. (The strict `>`
        # comparison preserves the existing longest-wins behavior
        # for pre-v2.17.3 callers and avoids no-op writes.)
        out = _resolve_reenrich_description(
            current="The exact same description.",
            enriched="The exact same description.",
        )
        assert out is None
