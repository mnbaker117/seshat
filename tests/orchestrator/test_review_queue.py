"""
Integration tests for the review-queue path through the pipeline.

Exercises:
  - `process_completion` with review_queue_enabled=True stops at
    the review_queue row and leaves the staged file in place
  - `deliver_reviewed` ships an approved item via the sink and
    marks the queue row delivered
  - `deliver_reviewed` records a calibre_additions counter row
  - `review_timeout.tick()` auto-delivers items past the grace
    period with was_timeout=True
"""
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from app.database import get_db
from app.orchestrator.dispatch import DispatcherDeps
from app.orchestrator.download_watcher import CompletionEvent
from app.orchestrator.pipeline import deliver_reviewed, process_completion
from app.orchestrator.review_timeout import tick as review_timeout_tick
from app.storage import calibre_adds as calibre_adds_storage
from app.storage import grabs as grabs_storage
from app.storage import pipeline as pipe_storage
from app.storage import review_queue as review_storage


def _make_epub(path: Path, title: str = "Test Book", author: str = "Test Author"):
    opf = ET.Element("package", xmlns="http://www.idpf.org/2007/opf", version="3.0")
    md = ET.SubElement(opf, "metadata")
    md.set("xmlns:dc", "http://purl.org/dc/elements/1.1/")
    dc_title = ET.SubElement(md, "dc:title")
    dc_title.text = title
    dc_creator = ET.SubElement(md, "dc:creator")
    dc_creator.text = author
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(path), "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", ET.tostring(opf, encoding="unicode", xml_declaration=True))
        zf.writestr("chapter1.xhtml", "<html><body>Content</body></html>")


async def _setup_grab_and_event(db, tmp_path) -> CompletionEvent:
    grab_id = await grabs_storage.create_grab(
        db, announce_id=None, mam_torrent_id="12345",
        torrent_name="Test Book", category="ebooks fantasy",
        author_blob="Test Author", state=grabs_storage.STATE_DOWNLOADED,
    )
    source_dir = tmp_path / "downloads" / "Test Book"
    _make_epub(source_dir / "Test Book.epub")
    run_id = await pipe_storage.create_run(
        db, grab_id=grab_id, qbit_hash="hash_abc", source_path=str(source_dir),
    )
    return CompletionEvent(
        grab_id=grab_id, qbit_hash="hash_abc", torrent_name="Test Book",
        save_path=str(source_dir), pipeline_run_id=run_id,
    )


class TestReviewQueueFlow:
    async def test_process_completion_stops_at_review_queue(self, temp_db, tmp_path):
        db = await get_db()
        try:
            event = await _setup_grab_and_event(db, tmp_path)
            review_dir = tmp_path / "review-staging"
            library = tmp_path / "library"

            ok = await process_completion(
                db, event,
                staging_path="",
                default_sink="folder",
                calibre_library_path="",
                folder_sink_path=str(library),
                category_routing={},
                ntfy_url="", ntfy_topic="",
                review_queue_enabled=True,
                review_staging_path=str(review_dir),
            )
            assert ok is True

            # Library should be empty — sink wasn't called.
            assert not library.exists() or not any(library.rglob("*.epub"))

            # Review queue should contain a pending row.
            pending = await review_storage.list_pending(db)
            assert len(pending) == 1
            row = pending[0]
            assert row.status == review_storage.STATUS_PENDING
            assert row.grab_id == event.grab_id
            assert row.book_filename.endswith(".epub")
            assert row.metadata.get("author") == "Test Author"

            # Staged file exists on disk.
            staged = Path(row.staged_path) / row.book_filename
            assert staged.exists()

            # Pipeline run transitioned to awaiting_review.
            run = await pipe_storage.get_run(db, event.pipeline_run_id)
            assert run.state == pipe_storage.PIPE_AWAITING_REVIEW
        finally:
            await db.close()

    async def test_deliver_reviewed_ships_approved_book(self, temp_db, tmp_path):
        db = await get_db()
        try:
            event = await _setup_grab_and_event(db, tmp_path)
            review_dir = tmp_path / "review-staging"
            library = tmp_path / "library"

            await process_completion(
                db, event,
                staging_path="",
                default_sink="folder",
                calibre_library_path="",
                folder_sink_path=str(library),
                category_routing={},
                ntfy_url="", ntfy_topic="",
                review_queue_enabled=True,
                review_staging_path=str(review_dir),
            )
            pending = await review_storage.list_pending(db)
            review_id = pending[0].id

            ok = await deliver_reviewed(
                db, review_id=review_id,
                default_sink="folder",
                calibre_library_path="",
                folder_sink_path=str(library),
                ntfy_url="", ntfy_topic="",
                auto_train_enabled=True,
                was_timeout=False,
            )
            assert ok is True

            # Book now in library.
            epubs = list(library.rglob("*.epub"))
            assert len(epubs) == 1

            # Review row marked delivered.
            refreshed = await review_storage.get_entry(db, review_id)
            assert refreshed.status == review_storage.STATUS_DELIVERED

            # Pipeline run marked complete.
            run = await pipe_storage.get_run(db, event.pipeline_run_id)
            assert run.state == pipe_storage.PIPE_COMPLETE

            # Grab marked complete.
            grab = await grabs_storage.get_grab(db, event.grab_id)
            assert grab.state == grabs_storage.STATE_COMPLETE

            # calibre_additions counter row exists.
            count = await calibre_adds_storage.count_since(db, hours=1)
            assert count == 1
            rows = await calibre_adds_storage.list_since(db, hours=1)
            assert rows[0].was_timeout is False
            assert rows[0].title == "Test Book"
        finally:
            await db.close()

    async def test_deliver_reviewed_rejects_already_decided(self, temp_db, tmp_path):
        db = await get_db()
        try:
            event = await _setup_grab_and_event(db, tmp_path)
            review_dir = tmp_path / "review-staging"
            library = tmp_path / "library"
            await process_completion(
                db, event,
                staging_path="",
                default_sink="folder",
                calibre_library_path="",
                folder_sink_path=str(library),
                category_routing={},
                ntfy_url="", ntfy_topic="",
                review_queue_enabled=True,
                review_staging_path=str(review_dir),
            )
            pending = await review_storage.list_pending(db)
            review_id = pending[0].id
            await review_storage.set_status(
                db, review_id, review_storage.STATUS_REJECTED,
                decision_note="user said no",
            )
            # Trying to deliver a rejected item is a no-op, not an error.
            ok = await deliver_reviewed(
                db, review_id=review_id,
                default_sink="folder",
                calibre_library_path="",
                folder_sink_path=str(library),
                ntfy_url="", ntfy_topic="",
            )
            assert ok is False
            assert not any(library.rglob("*.epub"))
        finally:
            await db.close()


class TestReviewTimeoutJob:
    async def test_tick_auto_delivers_stale_items(self, temp_db, tmp_path):
        db = await get_db()
        try:
            event = await _setup_grab_and_event(db, tmp_path)
            review_dir = tmp_path / "review-staging"
            library = tmp_path / "library"
            await process_completion(
                db, event,
                staging_path="",
                default_sink="folder",
                calibre_library_path="",
                folder_sink_path=str(library),
                category_routing={},
                ntfy_url="", ntfy_topic="",
                review_queue_enabled=True,
                review_staging_path=str(review_dir),
            )
            pending = await review_storage.list_pending(db)
            review_id = pending[0].id

            # Backdate the created_at so the row counts as stale.
            await db.execute(
                "UPDATE book_review_queue SET created_at = datetime('now', '-30 days') WHERE id = ?",
                (review_id,),
            )
            await db.commit()
        finally:
            await db.close()

        # Build a minimal DispatcherDeps for the tick to read from.
        # Only the pipeline-relevant fields matter; the rest can be
        # defaults since tick() doesn't touch qBit or MAM.
        from app.clients.base import AddResult, TorrentClient
        from app.filter.gate import FilterConfig

        class _NullQbit(TorrentClient):
            async def add_torrent(self, *a, **kw):
                return AddResult(success=False, failure_kind="rejected")
            async def list_torrents(self, *a, **kw):
                return []
            async def get_torrent(self, *a, **kw):
                return None
            async def aclose(self):
                pass

        async def _null_fetch(*a, **kw):
            raise AssertionError("should not fetch")

        deps = DispatcherDeps(
            filter_config=FilterConfig(allowed_categories=frozenset()),
            mam_token="", qbit_category="", budget_cap=0, queue_max=0,
            queue_mode_enabled=False, seed_seconds_required=0,
            db_factory=get_db,
            fetch_torrent=_null_fetch,
            qbit=_NullQbit(),
            review_queue_enabled=True,
            review_staging_path=str(review_dir),
            metadata_review_timeout_days=14,
            default_sink="folder",
            folder_sink_path=str(library),
        )

        delivered = await review_timeout_tick(deps)
        assert delivered == 1

        db2 = await get_db()
        try:
            refreshed = await review_storage.get_entry(db2, review_id)
            assert refreshed.status == review_storage.STATUS_DELIVERED
            assert "timeout" in (refreshed.decision_note or "")

            rows = await calibre_adds_storage.list_since(db2, hours=1)
            assert rows[0].was_timeout is True

            epubs = list(library.rglob("*.epub"))
            assert len(epubs) == 1
        finally:
            await db2.close()
