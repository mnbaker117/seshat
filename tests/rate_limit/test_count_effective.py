"""
Tests for the effective-budget count that includes manual qBit adds.

`count_effective` reads the ledger-active count and adds whatever
the budget watcher cached under `state._snatch_budget["qbit_extras"]`.
Ensures manual / Autobrr torrents in the watched category don't
let Seshat over-commit the MAM snatch cap.
"""
from app import state
from app.database import get_db
from app.rate_limit import ledger as ledger_mod
from app.storage import grabs as grabs_storage


async def _seed_grab(db, grab_id: int, mam_id: str) -> None:
    """Insert a minimal grab row so ledger FK constraints pass."""
    await db.execute(
        """
        INSERT INTO grabs (id, mam_torrent_id, torrent_name, state)
        VALUES (?, ?, ?, ?)
        """,
        (grab_id, mam_id, f"Book {grab_id}", grabs_storage.STATE_SUBMITTED),
    )
    await db.commit()


class TestCountEffective:
    async def test_zero_extras_equals_active(self, temp_db, monkeypatch):
        monkeypatch.setitem(state._snatch_budget, "qbit_extras", 0)
        db = await get_db()
        try:
            assert await ledger_mod.count_effective(db) == 0
            await _seed_grab(db, 1, "t1")
            await ledger_mod.record_grab(db, grab_id=1, qbit_hash="h1")
            assert await ledger_mod.count_effective(db) == 1
        finally:
            await db.close()

    async def test_extras_add_to_active(self, temp_db, monkeypatch):
        monkeypatch.setitem(state._snatch_budget, "qbit_extras", 5)
        db = await get_db()
        try:
            assert await ledger_mod.count_effective(db) == 5
            await _seed_grab(db, 1, "t1")
            await _seed_grab(db, 2, "t2")
            await ledger_mod.record_grab(db, grab_id=1, qbit_hash="h1")
            await ledger_mod.record_grab(db, grab_id=2, qbit_hash="h2")
            assert await ledger_mod.count_effective(db) == 7
        finally:
            await db.close()

    async def test_negative_extras_clamped(self, temp_db, monkeypatch):
        monkeypatch.setitem(state._snatch_budget, "qbit_extras", -3)
        db = await get_db()
        try:
            await _seed_grab(db, 1, "t1")
            await ledger_mod.record_grab(db, grab_id=1, qbit_hash="h1")
            # Negative is nonsense — treat as zero, not a discount.
            assert await ledger_mod.count_effective(db) == 1
        finally:
            await db.close()
