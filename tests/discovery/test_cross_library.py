"""
Tests for `app.discovery.cross_library` — the per-library fan-out helpers
that back every cross-library aggregated endpoint (Works, missing,
authors, etc.).

The module is small but load-bearing: a regression in `libraries_for` or
`run_across_libraries` silently reshapes results on the Works page and
the cross-library views. These tests cover:

  * library filtering by content_type
  * per-library stamping (slug, content_type, library_name)
  * error degradation — one library failing doesn't poison the whole
    aggregation, per the docstring guarantee
  * sort_key_for / sort_and_paginate mechanics (stable, None-safe)
"""
from __future__ import annotations

import pytest

from app.discovery import cross_library as cx


# ─── libraries_for ────────────────────────────────────────────

class TestLibrariesFor:
    def test_all_or_none_returns_every_library(self, monkeypatch):
        libs = [
            {"slug": "cal", "content_type": "ebook"},
            {"slug": "abs", "content_type": "audiobook"},
        ]
        monkeypatch.setattr(
            "app.discovery.cross_library.state._discovered_libraries", libs,
        )
        assert cx.libraries_for(None) == libs
        assert cx.libraries_for("") == libs
        assert cx.libraries_for("all") == libs

    def test_filters_to_ebook_only(self, monkeypatch):
        libs = [
            {"slug": "cal", "content_type": "ebook"},
            {"slug": "abs", "content_type": "audiobook"},
        ]
        monkeypatch.setattr(
            "app.discovery.cross_library.state._discovered_libraries", libs,
        )
        out = cx.libraries_for("ebook")
        assert [l["slug"] for l in out] == ["cal"]

    def test_filters_to_audiobook_only(self, monkeypatch):
        libs = [
            {"slug": "cal", "content_type": "ebook"},
            {"slug": "abs", "content_type": "audiobook"},
        ]
        monkeypatch.setattr(
            "app.discovery.cross_library.state._discovered_libraries", libs,
        )
        out = cx.libraries_for("audiobook")
        assert [l["slug"] for l in out] == ["abs"]

    def test_missing_content_type_defaults_to_ebook(self, monkeypatch):
        """A library dict without content_type should be treated as ebook."""
        libs = [{"slug": "cal"}, {"slug": "abs", "content_type": "audiobook"}]
        monkeypatch.setattr(
            "app.discovery.cross_library.state._discovered_libraries", libs,
        )
        out = cx.libraries_for("ebook")
        assert [l["slug"] for l in out] == ["cal"]

    def test_empty_result_when_no_matching_library(self, monkeypatch):
        libs = [{"slug": "cal", "content_type": "ebook"}]
        monkeypatch.setattr(
            "app.discovery.cross_library.state._discovered_libraries", libs,
        )
        assert cx.libraries_for("audiobook") == []


# ─── run_across_libraries ─────────────────────────────────────

@pytest.fixture
def fake_libs(monkeypatch):
    """Install two fake libraries whose get_db returns in-memory row lists.

    `get_db` in the real module opens aiosqlite; we monkeypatch it to
    return a lightweight object whose `close()` coroutine is the only
    API cross_library touches. The actual "query" is a caller-supplied
    function, so the fake_db object is just a tag the test inspects.
    """
    libs = [
        {"slug": "cal", "name": "Calibre", "content_type": "ebook"},
        {"slug": "abs", "name": "ABS", "content_type": "audiobook"},
    ]
    monkeypatch.setattr(
        "app.discovery.cross_library.state._discovered_libraries", libs,
    )

    class FakeDB:
        def __init__(self, slug):
            self.slug = slug

        async def close(self):
            pass

    async def fake_get_db(slug):
        return FakeDB(slug)

    monkeypatch.setattr(
        "app.discovery.cross_library.get_library_db", fake_get_db,
    )
    return libs


class TestRunAcrossLibraries:
    async def test_stamps_slug_name_and_content_type_per_row(self, fake_libs):
        async def query(db):
            return [{"id": 1, "title": f"book-{db.slug}"}]

        rows = await cx.run_across_libraries("all", query)
        assert len(rows) == 2
        by_slug = {r["library_slug"]: r for r in rows}
        assert by_slug["cal"]["library_name"] == "Calibre"
        assert by_slug["cal"]["content_type"] == "ebook"
        assert by_slug["abs"]["library_name"] == "ABS"
        assert by_slug["abs"]["content_type"] == "audiobook"

    async def test_preserves_row_level_content_type(self, fake_libs):
        """A row that already has content_type keeps it — future-proof for
        mixed-content libraries."""
        async def query(db):
            return [{"id": 1, "content_type": "comic"}]

        rows = await cx.run_across_libraries("all", query)
        assert all(r["content_type"] == "comic" for r in rows)

    async def test_filters_by_content_type(self, fake_libs):
        async def query(db):
            return [{"id": 1, "slug_tag": db.slug}]

        rows = await cx.run_across_libraries("ebook", query)
        assert [r["slug_tag"] for r in rows] == ["cal"]

    async def test_one_broken_library_does_not_block_the_others(
        self, fake_libs, caplog,
    ):
        async def query(db):
            if db.slug == "cal":
                raise RuntimeError("simulated query failure")
            return [{"id": 99, "title": "abs book"}]

        with caplog.at_level("WARNING", logger="seshat.discovery.cross_library"):
            rows = await cx.run_across_libraries("all", query)

        # ABS result survived.
        assert len(rows) == 1
        assert rows[0]["library_slug"] == "abs"
        # Warning emitted for the broken one.
        assert any("cross-library query failed" in rec.message for rec in caplog.records)

    async def test_open_failure_is_warned_and_skipped(
        self, monkeypatch, caplog,
    ):
        libs = [
            {"slug": "cal", "name": "Calibre", "content_type": "ebook"},
            {"slug": "abs", "name": "ABS", "content_type": "audiobook"},
        ]
        monkeypatch.setattr(
            "app.discovery.cross_library.state._discovered_libraries", libs,
        )

        class FakeDB:
            def __init__(self, slug): self.slug = slug
            async def close(self): pass

        async def fake_get_db(slug):
            if slug == "cal":
                raise OSError("no such file")
            return FakeDB(slug)

        monkeypatch.setattr(
            "app.discovery.cross_library.get_library_db", fake_get_db,
        )

        async def query(db):
            return [{"id": 1}]

        with caplog.at_level("WARNING", logger="seshat.discovery.cross_library"):
            rows = await cx.run_across_libraries("all", query)

        assert len(rows) == 1
        assert rows[0]["library_slug"] == "abs"
        assert any("cross-library open failed" in rec.message for rec in caplog.records)

    async def test_skips_libraries_without_slug(self, monkeypatch):
        libs = [
            {"name": "missing slug", "content_type": "ebook"},
            {"slug": "abs", "name": "ABS", "content_type": "audiobook"},
        ]
        monkeypatch.setattr(
            "app.discovery.cross_library.state._discovered_libraries", libs,
        )

        class FakeDB:
            def __init__(self, slug): self.slug = slug
            async def close(self): pass

        async def fake_get_db(slug):
            return FakeDB(slug)

        monkeypatch.setattr(
            "app.discovery.cross_library.get_library_db", fake_get_db,
        )

        async def query(db):
            return [{"id": 1}]

        rows = await cx.run_across_libraries("all", query)
        # Only the lib with a slug got queried.
        assert len(rows) == 1
        assert rows[0]["library_slug"] == "abs"


# ─── sort_and_paginate ────────────────────────────────────────

class TestSortAndPaginate:
    def test_empty_input(self):
        window, total = cx.sort_and_paginate(
            [], sort_key=cx.SORT_KEYS["title"], reverse=False,
            page=1, per_page=10,
        )
        assert window == []
        assert total == 0

    def test_sort_and_slice(self):
        rows = [
            {"title": "Charlie"},
            {"title": "alice"},
            {"title": "Bob"},
        ]
        window, total = cx.sort_and_paginate(
            rows, sort_key=cx.SORT_KEYS["title"], reverse=False,
            page=1, per_page=10,
        )
        assert total == 3
        # Case-folded comparison: alice < Bob < Charlie
        assert [r["title"] for r in window] == ["alice", "Bob", "Charlie"]

    def test_reverse_flips_order(self):
        rows = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
        window, _ = cx.sort_and_paginate(
            rows, sort_key=cx.SORT_KEYS["title"], reverse=True,
            page=1, per_page=10,
        )
        assert [r["title"] for r in window] == ["c", "b", "a"]

    def test_second_page_slice(self):
        rows = [{"title": f"t{i:02d}"} for i in range(25)]
        window, total = cx.sort_and_paginate(
            rows, sort_key=cx.SORT_KEYS["title"], reverse=False,
            page=2, per_page=10,
        )
        assert total == 25
        assert [r["title"] for r in window] == [f"t{i:02d}" for i in range(10, 20)]

    def test_past_last_page_is_empty(self):
        rows = [{"title": "a"}, {"title": "b"}]
        window, total = cx.sort_and_paginate(
            rows, sort_key=cx.SORT_KEYS["title"], reverse=False,
            page=5, per_page=10,
        )
        assert total == 2
        assert window == []

    def test_stable_sort_keeps_library_order_within_tie(self):
        """Two rows with identical sort keys should keep their input order —
        pagination across libraries depends on this."""
        rows = [
            {"title": "same", "library_slug": "cal"},
            {"title": "same", "library_slug": "abs"},
        ]
        window, _ = cx.sort_and_paginate(
            rows, sort_key=cx.SORT_KEYS["title"], reverse=False,
            page=1, per_page=10,
        )
        assert [r["library_slug"] for r in window] == ["cal", "abs"]


# ─── SORT_KEYS / sort_key_for ─────────────────────────────────

class TestSortKeys:
    def test_sort_key_for_unknown_falls_back_to_title(self):
        assert cx.sort_key_for("bogus") is cx.SORT_KEYS["title"]

    def test_sort_key_for_known(self):
        assert cx.sort_key_for("series") is cx.SORT_KEYS["series"]

    def test_title_key_is_none_safe(self):
        key = cx.SORT_KEYS["title"]
        assert key({"title": None}) == ("",)

    def test_author_key_falls_back_to_author_name(self):
        key = cx.SORT_KEYS["author"]
        out = key({"author_name": "Asimov", "title": "Foundation"})
        assert out == ("asimov", "foundation")

    def test_series_key_nulls_sort_last(self):
        """v2.11.1 N2: has-series rows sort before no-series rows
        on ASC. First tuple slot is 0 for has-series, 1 for none,
        so Python's tuple comparison puts has-series first."""
        key = cx.SORT_KEYS["series"]
        has = key({"series_name": "Mistborn", "series_index": 1.0})
        none = key({"series_name": None})
        assert has < none
        # Slot 0 carries the has-series boolean (0 = has, 1 = none).
        assert has[0] == 0
        assert none[0] == 1

    def test_series_key_orders_by_index_within_same_series(self):
        """Within one series, books order by series_index, then
        title for the same-index dedup case."""
        key = cx.SORT_KEYS["series"]
        book1 = key({
            "series_name": "Mistborn",
            "series_index": 1.0,
            "title": "Final Empire",
        })
        book2 = key({
            "series_name": "Mistborn",
            "series_index": 2.0,
            "title": "Well of Ascension",
        })
        assert book1 < book2

    def test_series_key_orders_by_series_name_across_series(self):
        """Different series sort alphabetically by series_name."""
        key = cx.SORT_KEYS["series"]
        cosmere_a = key({
            "series_name": "Alcatraz",
            "series_index": 1.0,
        })
        cosmere_m = key({
            "series_name": "Mistborn",
            "series_index": 1.0,
        })
        assert cosmere_a < cosmere_m

    def test_series_sort_full_list_null_last_ascending(self):
        """Integration: sort a mixed list and confirm no-series
        rows land at the END on ascending sort."""
        rows = [
            {"series_name": None, "title": "Standalone One"},
            {"series_name": "Mistborn", "series_index": 2.0, "title": "Well"},
            {"series_name": None, "title": "Standalone Two"},
            {"series_name": "Mistborn", "series_index": 1.0, "title": "Final"},
            {"series_name": "Alcatraz", "series_index": 1.0, "title": "Evil"},
        ]
        sorted_rows = sorted(rows, key=cx.SORT_KEYS["series"])
        # Has-series rows first, alphabetical by series_name then index
        assert sorted_rows[0]["series_name"] == "Alcatraz"
        assert sorted_rows[1]["title"] == "Final"
        assert sorted_rows[2]["title"] == "Well"
        # No-series rows at the end
        assert sorted_rows[3]["series_name"] is None
        assert sorted_rows[4]["series_name"] is None

    def test_name_key_falls_back_to_name(self):
        key = cx.SORT_KEYS["name"]
        assert key({"name": "Author"}) == ("author",)
        assert key({"sort_name": "Sort", "name": "Author"}) == ("sort",)
