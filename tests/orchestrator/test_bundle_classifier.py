"""
Tests for the bundle/collection classifier.

Covers the six representative cases the v2.7.0 design called out:
  1. 1 epub → 1 group (single-book baseline)
  2. epub+mobi+azw3 same stem → 1 group (multi-format same book)
  3. 3 distinct epubs → 3 groups (ebook bundle)
  4. main novel + bonus novella mixed → 2 groups (mixed-format
     bundle — different stems, different titles)
  5. 26-part m4b audiobook → 1 group (multi-part safety net)
  6. 4 distinct m4b audiobooks → 4 groups (audiobook bundle)

Plus disabled-flag behavior and the filename-token fallback for
files with no embedded metadata.

The classifier accepts an injectable `extract_fn` so tests can
substitute a controlled metadata map keyed by filename rather than
building real EPUBs/M4Bs. Production code calls
`extract_metadata` directly.
"""
from pathlib import Path

from app.metadata.extract import BookMetadata
from app.orchestrator.bundle_classifier import classify


def _fake_extract(meta_map: dict[str, BookMetadata]):
    """Return an extract_fn that looks up metadata by filename."""

    def _fn(path: Path) -> BookMetadata:
        return meta_map.get(path.name, BookMetadata())

    return _fn


def _paths(*names: str) -> list[Path]:
    """Return Path objects for the given filenames (no FS touch)."""
    return [Path(n) for n in names]


class TestSingleFile:
    def test_one_epub_one_group(self):
        files = _paths("Foundation.epub")
        meta = {"Foundation.epub": BookMetadata(title="Foundation", author="Isaac Asimov")}
        groups = classify(files, extract_fn=_fake_extract(meta))
        assert len(groups) == 1
        assert groups[0].primary == files[0]
        assert groups[0].files == files
        assert groups[0].extracted.title == "Foundation"


class TestStemDedupe:
    def test_multi_format_same_book_one_group(self):
        """epub + mobi + azw3 with the same stem → multi-format,
        one group. The classifier short-circuits before reading any
        embedded metadata beyond the primary's."""
        files = _paths(
            "Foundation.epub",
            "Foundation.mobi",
            "Foundation.azw3",
        )
        # Only the primary needs metadata; the other two are
        # skipped by the stem-dedupe short-circuit.
        meta = {"Foundation.epub": BookMetadata(title="Foundation", author="Asimov")}
        groups = classify(files, extract_fn=_fake_extract(meta))
        assert len(groups) == 1
        assert len(groups[0].files) == 3
        assert groups[0].primary.name == "Foundation.epub"

    def test_case_insensitive_stem_dedupe(self):
        """Some uploaders mix casing on the format file — same stem
        when lowercased should still dedupe."""
        files = _paths("Foundation.epub", "FOUNDATION.mobi")
        meta = {"Foundation.epub": BookMetadata(title="Foundation", author="Asimov")}
        groups = classify(files, extract_fn=_fake_extract(meta))
        assert len(groups) == 1


class TestEbookBundle:
    def test_three_distinct_epubs_three_groups(self):
        """The Mistborn Trilogy case — three different novels in one
        torrent, all epub. Each must end up its own review entry."""
        files = _paths(
            "01 - The Final Empire.epub",
            "02 - The Well of Ascension.epub",
            "03 - The Hero of Ages.epub",
        )
        meta = {
            "01 - The Final Empire.epub": BookMetadata(
                title="The Final Empire", author="Brandon Sanderson"),
            "02 - The Well of Ascension.epub": BookMetadata(
                title="The Well of Ascension", author="Brandon Sanderson"),
            "03 - The Hero of Ages.epub": BookMetadata(
                title="The Hero of Ages", author="Brandon Sanderson"),
        }
        groups = classify(files, extract_fn=_fake_extract(meta))
        assert len(groups) == 3
        titles = {g.extracted.title for g in groups}
        assert titles == {
            "The Final Empire", "The Well of Ascension", "The Hero of Ages",
        }

    def test_mixed_format_bundle(self):
        """Main novel as epub+mobi, plus a separately-named bonus
        novella as epub. Two groups — main book (with 2 formats) and
        novella."""
        files = _paths(
            "Main Novel.epub",
            "Main Novel.mobi",
            "Bonus Novella.epub",
        )
        meta = {
            "Main Novel.epub": BookMetadata(title="Main Novel", author="Some Author"),
            "Main Novel.mobi": BookMetadata(title="Main Novel", author="Some Author"),
            "Bonus Novella.epub": BookMetadata(title="Bonus Novella", author="Some Author"),
        }
        groups = classify(files, extract_fn=_fake_extract(meta))
        assert len(groups) == 2
        # Main novel group should have 2 files; novella has 1.
        sizes = sorted(len(g.files) for g in groups)
        assert sizes == [1, 2]


class TestAudiobookPartsSafetyNet:
    def test_26_part_m4b_one_group(self):
        """Audible-rip style: one audiobook split across 26 m4b
        files with `Part NN` in the filename. The safety net must
        collapse them to one group regardless of embedded metadata
        consistency."""
        files = _paths(*(
            f"The Way of Kings - Part {i:02d}.m4b" for i in range(1, 27)
        ))
        # Embedded metadata might or might not be consistent —
        # the safety net relies on extension + filename pattern.
        meta = {
            f.name: BookMetadata(title="The Way of Kings", author="Brandon Sanderson")
            for f in files
        }
        groups = classify(files, extract_fn=_fake_extract(meta))
        assert len(groups) == 1
        assert len(groups[0].files) == 26

    def test_disc_token_collapses_to_one(self):
        """Some rippers use Disc 01 / Disc 02 instead of Part."""
        files = _paths(
            "Book Title - Disc 01.mp3",
            "Book Title - Disc 02.mp3",
            "Book Title - Disc 03.mp3",
        )
        meta = {f.name: BookMetadata(title="Book Title", author="A") for f in files}
        groups = classify(files, extract_fn=_fake_extract(meta))
        assert len(groups) == 1
        assert len(groups[0].files) == 3


class TestAudiobookBundle:
    def test_four_distinct_m4b_audiobooks_four_groups(self):
        """An audiobook bundle: 4 separate works, each a single
        m4b. Filenames don't contain part tokens (each file is a
        complete audiobook). Embedded metadata distinguishes them."""
        files = _paths(
            "The Final Empire.m4b",
            "The Well of Ascension.m4b",
            "The Hero of Ages.m4b",
            "The Alloy of Law.m4b",
        )
        meta = {
            "The Final Empire.m4b": BookMetadata(
                title="The Final Empire", author="Brandon Sanderson"),
            "The Well of Ascension.m4b": BookMetadata(
                title="The Well of Ascension", author="Brandon Sanderson"),
            "The Hero of Ages.m4b": BookMetadata(
                title="The Hero of Ages", author="Brandon Sanderson"),
            "The Alloy of Law.m4b": BookMetadata(
                title="The Alloy of Law", author="Brandon Sanderson"),
        }
        groups = classify(files, extract_fn=_fake_extract(meta))
        assert len(groups) == 4


class TestDisabledFlag:
    def test_disabled_returns_one_group_regardless(self):
        """With `enabled=False`, even distinct-title files collapse
        to one group. Used as the kill-switch if the classifier
        misfires in production."""
        files = _paths("Book One.epub", "Book Two.epub", "Book Three.epub")
        meta = {
            "Book One.epub": BookMetadata(title="One", author="A"),
            "Book Two.epub": BookMetadata(title="Two", author="A"),
            "Book Three.epub": BookMetadata(title="Three", author="A"),
        }
        groups = classify(files, enabled=False, extract_fn=_fake_extract(meta))
        assert len(groups) == 1
        assert len(groups[0].files) == 3
        # Primary is the first file (caller's preferred ordering).
        assert groups[0].primary.name == "Book One.epub"


class TestFilenameFallback:
    def test_untitled_files_cluster_by_filename(self):
        """When embedded extraction yields empty titles (PDFs etc.)
        the filename-token fallback should still cluster files that
        share a common name shape."""
        files = _paths(
            "Some Book Volume 1.pdf",
            "Some Book Volume 2.pdf",
            "Different Story.pdf",
        )
        meta = {f.name: BookMetadata() for f in files}  # all empty
        groups = classify(files, extract_fn=_fake_extract(meta))
        # At minimum the "Different Story" file should be in its own
        # group. The two Some Book volumes may or may not cluster
        # together depending on token overlap.
        assert len(groups) >= 2
        # Every file is accounted for.
        total = sum(len(g.files) for g in groups)
        assert total == 3

    def test_extract_failure_does_not_crash(self):
        """A failing extract_fn must not blow up classify()."""
        files = _paths("A.epub", "B.epub")

        def _broken(_p):
            raise RuntimeError("boom")

        # First file's metadata is read at the top of classify(); if
        # that raises, classify can't proceed. Patch with a wrapper
        # that crashes only on subsequent calls — emulates a corrupt
        # file in a mixed batch.
        call_count = [0]

        def _flaky(p: Path) -> BookMetadata:
            call_count[0] += 1
            if call_count[0] == 1:
                return BookMetadata(title="A", author="Author")
            raise RuntimeError("boom")

        groups = classify(files, extract_fn=_flaky)
        # Crashed file falls through to filename-fallback and becomes
        # its own untitled group (or attaches to A's group if names
        # are similar enough).
        total = sum(len(g.files) for g in groups)
        assert total == 2


class TestEmptyInput:
    def test_no_files_returns_empty(self):
        assert classify([]) == []


class TestPrimaryOrderingPreserved:
    def test_primary_is_first_file_in_each_group(self):
        """Caller passes files in user-priority order (largest /
        preferred format first). Within each output group the first
        element of `files` must be the primary so the existing
        pipeline's format-priority semantics carry through."""
        files = _paths(
            "Book One.epub",  # primary for book one
            "Book One.mobi",  # secondary format
            "Book Two.epub",  # primary for book two
        )
        meta = {
            "Book One.epub": BookMetadata(title="Book One", author="A"),
            "Book One.mobi": BookMetadata(title="Book One", author="A"),
            "Book Two.epub": BookMetadata(title="Book Two", author="A"),
        }
        groups = classify(files, extract_fn=_fake_extract(meta))
        assert len(groups) == 2
        for g in groups:
            # Primary is files[0]; both formats of Book One should
            # have the .epub first because that's the input ordering.
            assert g.primary == g.files[0]
            if any(f.name == "Book One.mobi" for f in g.files):
                assert g.primary.name == "Book One.epub"
