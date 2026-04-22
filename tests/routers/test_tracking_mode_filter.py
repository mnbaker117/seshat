"""
Tests for `_apply_tracking_mode_filter` in `app.discovery.routers.books`.

The filter runs on aggregated cross-library results and drops rows
whose author's tracking_mode excludes the row's content_type. Global
default comes from `settings.audiobook_tracking_mode`, with per-author
overrides winning.

Behavior by `content_type` request param:
  * "all"       — keep books whose row content_type matches the mode
  * "ebook"     — keep only rows where mode allows ebook
  * "audiobook" — keep only rows where mode allows audiobook
  * mode="both" always keeps everything

Shared setup: a fresh pipeline DB via `temp_db` + a canned settings
dict injected through `load_settings` monkeypatch.
"""
from __future__ import annotations

import pytest

from app.discovery.routers.books import _apply_tracking_mode_filter
from app.works import preferences


def _book(author: str, content_type: str = "ebook", title: str = "T") -> dict:
    return {
        "title": title,
        "author_name": author,
        "content_type": content_type,
    }


@pytest.fixture
def set_global_mode(monkeypatch):
    """Return a setter that patches the global audiobook_tracking_mode."""
    state = {"mode": "both"}

    def apply(mode: str) -> None:
        state["mode"] = mode

    monkeypatch.setattr(
        "app.config.load_settings",
        lambda: {"audiobook_tracking_mode": state["mode"]},
    )
    return apply


class TestGlobalDefault:
    async def test_mode_both_keeps_everything(self, temp_db, set_global_mode):
        set_global_mode("both")
        books = [
            _book("Alice", "ebook"),
            _book("Alice", "audiobook"),
            _book("Bob", "ebook"),
        ]
        out = await _apply_tracking_mode_filter(books, "all")
        assert len(out) == 3

    async def test_mode_ebook_drops_audiobook_rows_under_all(
        self, temp_db, set_global_mode,
    ):
        set_global_mode("ebook")
        books = [
            _book("Alice", "ebook"),
            _book("Alice", "audiobook"),
        ]
        out = await _apply_tracking_mode_filter(books, "all")
        assert [b["content_type"] for b in out] == ["ebook"]

    async def test_mode_audiobook_drops_ebook_rows_under_all(
        self, temp_db, set_global_mode,
    ):
        set_global_mode("audiobook")
        books = [
            _book("Alice", "ebook"),
            _book("Alice", "audiobook"),
        ]
        out = await _apply_tracking_mode_filter(books, "all")
        assert [b["content_type"] for b in out] == ["audiobook"]


class TestPerAuthorOverride:
    async def test_override_wins_over_global(self, temp_db, set_global_mode):
        """Bob has ebook override; global is audiobook. Bob's ebook row
        survives, others don't."""
        set_global_mode("audiobook")
        await preferences.set_preference("Bob", "ebook")

        books = [
            _book("Alice", "ebook"),       # global audiobook → drop
            _book("Alice", "audiobook"),   # global audiobook → keep
            _book("Bob", "ebook"),         # override ebook → keep
            _book("Bob", "audiobook"),     # override ebook → drop
        ]
        out = await _apply_tracking_mode_filter(books, "all")
        authors_formats = {(b["author_name"], b["content_type"]) for b in out}
        assert authors_formats == {
            ("Alice", "audiobook"), ("Bob", "ebook"),
        }

    async def test_override_both_always_keeps(self, temp_db, set_global_mode):
        set_global_mode("ebook")
        await preferences.set_preference("Bob", "both")

        books = [
            _book("Alice", "audiobook"),  # global ebook → drop
            _book("Bob", "audiobook"),    # override both → keep
            _book("Bob", "ebook"),        # override both → keep
        ]
        out = await _apply_tracking_mode_filter(books, "all")
        assert len(out) == 2
        assert all(b["author_name"] == "Bob" for b in out)


class TestContentTypeNarrowed:
    async def test_ebook_request_filters_by_ebook_mode(
        self, temp_db, set_global_mode,
    ):
        """content_type="ebook" — mode must include ebook to survive.

        The row content_type is not re-checked here because the request
        already narrowed the upstream query to ebook rows. That's why
        the filter compares mode against the request, not the row.
        """
        set_global_mode("audiobook")
        await preferences.set_preference("Bob", "ebook")

        books = [
            _book("Alice", "ebook"),  # global audiobook → drop
            _book("Bob", "ebook"),    # override ebook → keep
        ]
        out = await _apply_tracking_mode_filter(books, "ebook")
        assert len(out) == 1
        assert out[0]["author_name"] == "Bob"

    async def test_audiobook_request_filters_by_audiobook_mode(
        self, temp_db, set_global_mode,
    ):
        set_global_mode("ebook")
        await preferences.set_preference("Bob", "audiobook")

        books = [
            _book("Alice", "audiobook"),  # global ebook → drop
            _book("Bob", "audiobook"),    # override audiobook → keep
        ]
        out = await _apply_tracking_mode_filter(books, "audiobook")
        assert len(out) == 1
        assert out[0]["author_name"] == "Bob"

    async def test_both_mode_keeps_regardless_of_request(
        self, temp_db, set_global_mode,
    ):
        set_global_mode("both")
        books = [
            _book("Alice", "ebook"),
            _book("Alice", "audiobook"),
        ]
        assert len(await _apply_tracking_mode_filter(books, "ebook")) == 2
        assert len(await _apply_tracking_mode_filter(books, "audiobook")) == 2


class TestEdgeCases:
    async def test_normalized_name_sharing_across_casing(
        self, temp_db, set_global_mode,
    ):
        """Per-author prefs are keyed by normalized name — "brandon
        sanderson" and "Brandon Sanderson" share one preference row."""
        set_global_mode("ebook")
        await preferences.set_preference("brandon sanderson", "audiobook")

        books = [
            _book("Brandon Sanderson", "audiobook"),
            _book("Brandon Sanderson", "ebook"),
        ]
        out = await _apply_tracking_mode_filter(books, "all")
        # Override = audiobook, so only the audiobook row survives.
        assert [b["content_type"] for b in out] == ["audiobook"]

    async def test_row_without_content_type_defaults_to_ebook(
        self, temp_db, set_global_mode,
    ):
        set_global_mode("audiobook")
        books = [{"author_name": "Alice", "title": "t"}]
        # No content_type on the row → treated as ebook → filtered out.
        assert await _apply_tracking_mode_filter(books, "all") == []

    async def test_row_without_author_uses_global(
        self, temp_db, set_global_mode,
    ):
        set_global_mode("ebook")
        books = [{"title": "anon", "content_type": "ebook"}]
        out = await _apply_tracking_mode_filter(books, "all")
        assert len(out) == 1

    async def test_empty_input_is_empty_output(self, temp_db, set_global_mode):
        set_global_mode("both")
        assert await _apply_tracking_mode_filter([], "all") == []
