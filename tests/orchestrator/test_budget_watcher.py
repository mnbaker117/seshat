"""
Unit tests for the snatch budget watcher loop.

Tests target the `tick()` function rather than `run_loop()` because
the loop is just `tick + sleep + repeat` — the interesting behavior
is all in the tick. The smoke test in `tests/test_lifespan_smoke.py`
exercises `run_loop()` end-to-end.

Coverage targets:
  - tick() reconciles seedtime: rows above threshold get released
  - tick() reconciles removal: rows missing from qBit get released
  - tick() pops from pending_queue when budget has room
  - tick() does NOT pop when budget is still full after reconcile
  - tick() drains the queue until budget fills again
  - Pop failure (fetch error) marks the grab failed and stops popping
  - Pop failure (qBit error) marks the grab failed and stops popping
  - tick() captures unexpected exceptions in TickResult.error
"""
from typing import Optional

from app.clients.base import AddResult, TorrentInfo
from app.database import get_db
from app.filter.gate import FilterConfig
from app.mam.grab import GrabResult
from app.orchestrator.budget_watcher import tick
from app.orchestrator.dispatch import DispatcherDeps
from app.rate_limit import ledger as ledger_mod
from app.rate_limit import queue as queue_mod
from app.storage import grabs as grabs_storage
from tests.fake_mam import MINIMAL_BENCODED_TORRENT
from tests.rate_limit._helpers import insert_dummy_grab


# ─── Fakes ───────────────────────────────────────────────────


class _FakeQbit:
    """Records add_torrent calls and returns programmable list_torrents."""

    def __init__(
        self,
        *,
        torrents: list[TorrentInfo] = None,
        add_result: Optional[AddResult] = None,
    ):
        self._torrents = torrents or []
        self.add_result = add_result or AddResult(success=True)
        self.add_calls: list[dict] = []

    async def login(self) -> bool:
        return True

    async def add_torrent(
        self,
        torrent_bytes: bytes,
        category: Optional[str] = None,
        save_path: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> AddResult:
        self.add_calls.append(
            {"size": len(torrent_bytes), "category": category, "tags": tags}
        )
        return self.add_result

    async def list_torrents(
        self, category: Optional[str] = None
    ) -> list[TorrentInfo]:
        return list(self._torrents)

    async def get_torrent(self, torrent_hash: str) -> Optional[TorrentInfo]:
        return None

    async def aclose(self) -> None:
        return None


def _make_fetch(result: GrabResult = None):
    """Build a fetch_torrent fake that always returns a fixed result."""
    final = result or GrabResult(
        success=True, torrent_bytes=MINIMAL_BENCODED_TORRENT
    )
    calls: list[tuple[str, str]] = []

    async def fake_fetch(torrent_id: str, token: str) -> GrabResult:
        calls.append((torrent_id, token))
        return final

    fake_fetch.calls = calls  # type: ignore[attr-defined]
    return fake_fetch


def _make_deps(
    *,
    qbit: _FakeQbit = None,
    fetch_result: GrabResult = None,
    budget_cap: int = 200,
) -> DispatcherDeps:
    return DispatcherDeps(
        filter_config=FilterConfig(
            allowed_categories=frozenset(),
            allowed_authors=frozenset(),
            ignored_authors=frozenset(),
        ),
        mam_token="test",
        qbit_category="mam-complete",
        budget_cap=budget_cap,
        queue_max=100,
        queue_mode_enabled=True,
        seed_seconds_required=72 * 3600,
        db_factory=get_db,
        fetch_torrent=_make_fetch(fetch_result),
        qbit=qbit or _FakeQbit(),
    )


def _info(hash_: str, seeding_seconds: int) -> TorrentInfo:
    return TorrentInfo(
        hash=hash_,
        name=f"name-{hash_}",
        category="mam-complete",
        state="uploading",
        seeding_seconds=seeding_seconds,
        save_path="/x",
        added_on=1,
    )


# ─── Reconcile phase ─────────────────────────────────────────


class TestReconcile:
    async def test_seedtime_release(self, temp_db):
        # Pre-populate ledger with one grab past threshold per qBit.
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger_mod.record_grab(db, grab_id, "h1")
        finally:
            await db.close()

        qbit = _FakeQbit(torrents=[_info("h1", 80 * 3600)])
        deps = _make_deps(qbit=qbit)

        result = await tick(deps)

        assert result.qbit_torrents_seen == 1
        assert result.seedtime_released == 1
        assert result.removed_released == 0
        assert result.error is None

        db = await get_db()
        try:
            assert await ledger_mod.count_active(db) == 0
        finally:
            await db.close()

    async def test_removal_release(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(db)
            await ledger_mod.record_grab(db, grab_id, "h_gone")
        finally:
            await db.close()

        # qBit reports nothing — user removed the torrent.
        qbit = _FakeQbit(torrents=[])
        deps = _make_deps(qbit=qbit)

        result = await tick(deps)

        assert result.removed_released == 1
        db = await get_db()
        try:
            assert await ledger_mod.count_active(db) == 0
        finally:
            await db.close()


# ─── Pop phase ───────────────────────────────────────────────


class TestPopFromQueue:
    async def test_pops_when_budget_has_room(self, temp_db):
        # Pre-populate the queue with one grab.
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(
                db, state=grabs_storage.STATE_PENDING_QUEUE
            )
            await queue_mod.enqueue(db, grab_id)
            assert await queue_mod.size(db) == 1
        finally:
            await db.close()

        qbit = _FakeQbit()  # default add_result success
        deps = _make_deps(qbit=qbit)
        result = await tick(deps)

        assert result.queue_pops_attempted == 1
        assert result.queue_pops_submitted == 1
        assert result.queue_pops_failed == 0
        assert len(qbit.add_calls) == 1

        db = await get_db()
        try:
            assert await queue_mod.size(db) == 0
            grab = await grabs_storage.get_grab(db, grab_id)
            assert grab.state == grabs_storage.STATE_SUBMITTED
            assert grab.qbit_hash is not None
            # Ledger should now have the new entry
            assert await ledger_mod.count_active(db) == 1
        finally:
            await db.close()

    async def test_does_not_pop_when_budget_full(self, temp_db):
        # Fill the budget to exactly cap, then verify nothing pops.
        db = await get_db()
        try:
            for i in range(3):
                grab_id = await insert_dummy_grab(db, torrent_id=str(i))
                await ledger_mod.record_grab(db, grab_id, f"h{i}")
            # And queue one
            queued_id = await insert_dummy_grab(
                db, torrent_id="queued", state=grabs_storage.STATE_PENDING_QUEUE
            )
            await queue_mod.enqueue(db, queued_id)
        finally:
            await db.close()

        # Cap = 3, all already in budget. qBit reports them all
        # well below threshold so reconcile doesn't release any.
        qbit = _FakeQbit(
            torrents=[
                _info("h0", 100),
                _info("h1", 100),
                _info("h2", 100),
            ]
        )
        deps = _make_deps(qbit=qbit, budget_cap=3)
        result = await tick(deps)

        assert result.queue_pops_attempted == 0
        assert qbit.add_calls == []
        db = await get_db()
        try:
            assert await queue_mod.size(db) == 1
            assert await ledger_mod.count_active(db) == 3
        finally:
            await db.close()

    async def test_drains_queue_after_reconcile_releases_room(self, temp_db):
        # Combined: ledger releases 2 rows, queue has 3 entries,
        # budget cap is 3 — so 2 entries should pop, 1 stays queued.
        db = await get_db()
        try:
            for i in range(3):
                grab_id = await insert_dummy_grab(db, torrent_id=f"active{i}")
                await ledger_mod.record_grab(db, grab_id, f"active{i}")
            queued_ids = []
            for i in range(3):
                qid = await insert_dummy_grab(
                    db, torrent_id=f"q{i}",
                    state=grabs_storage.STATE_PENDING_QUEUE,
                )
                await queue_mod.enqueue(db, qid)
                queued_ids.append(qid)
        finally:
            await db.close()

        # qBit reports two past threshold, one fresh — reconcile
        # will release the two and free 2 budget slots.
        qbit = _FakeQbit(
            torrents=[
                _info("active0", 80 * 3600),
                _info("active1", 80 * 3600),
                _info("active2", 100),
            ]
        )
        deps = _make_deps(qbit=qbit, budget_cap=3)
        result = await tick(deps)

        assert result.seedtime_released == 2
        assert result.queue_pops_submitted == 2
        assert len(qbit.add_calls) == 2

        db = await get_db()
        try:
            # 1 leftover queued + 2 newly submitted + 1 still active = 3
            assert await queue_mod.size(db) == 1
            assert await ledger_mod.count_active(db) == 3
        finally:
            await db.close()


# ─── Pop failure paths ───────────────────────────────────────


class TestPopFailures:
    async def test_fetch_failure_marks_grab(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(
                db, state=grabs_storage.STATE_PENDING_QUEUE
            )
            await queue_mod.enqueue(db, grab_id)
        finally:
            await db.close()

        deps = _make_deps(
            fetch_result=GrabResult(
                success=False,
                failure_kind="cookie_expired",
                failure_detail="login HTML",
            )
        )
        result = await tick(deps)

        assert result.queue_pops_attempted == 1
        assert result.queue_pops_submitted == 0
        assert result.queue_pops_failed == 1

        db = await get_db()
        try:
            grab = await grabs_storage.get_grab(db, grab_id)
            assert grab.state == grabs_storage.STATE_FAILED_COOKIE_EXPIRED
            # Queue should be drained — the failed grab IS popped,
            # just not successfully resubmitted.
            assert await queue_mod.size(db) == 0
        finally:
            await db.close()

    async def test_qbit_failure_marks_grab(self, temp_db):
        db = await get_db()
        try:
            grab_id = await insert_dummy_grab(
                db, state=grabs_storage.STATE_PENDING_QUEUE
            )
            await queue_mod.enqueue(db, grab_id)
        finally:
            await db.close()

        qbit = _FakeQbit(
            add_result=AddResult(
                success=False,
                failure_kind="rejected",
                failure_detail="HTTP 415",
            )
        )
        deps = _make_deps(qbit=qbit)
        result = await tick(deps)

        assert result.queue_pops_failed == 1
        db = await get_db()
        try:
            grab = await grabs_storage.get_grab(db, grab_id)
            assert grab.state == grabs_storage.STATE_FAILED_QBIT_REJECTED
        finally:
            await db.close()


# ─── Error handling ──────────────────────────────────────────


class TestErrorHandling:
    async def test_qbit_list_exception_captured(self, temp_db):
        class _BrokenQbit(_FakeQbit):
            async def list_torrents(self, category=None):
                raise RuntimeError("simulated qBit outage")

        deps = _make_deps(qbit=_BrokenQbit())
        result = await tick(deps)

        assert result.error is not None
        assert "RuntimeError" in result.error
        assert "simulated qBit outage" in result.error
