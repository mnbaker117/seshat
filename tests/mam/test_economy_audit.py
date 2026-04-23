"""
Unit tests for the `economy_audit` storage module.

Covers the insert contract, the list_recent read (with and without
action filter), the latest_success lookup, and the migration +
schema presence. Uses the `temp_db` fixture so every test gets a
fresh, fully-initialized SQLite file.
"""
from __future__ import annotations

from app.database import get_db
from app.storage import economy_audit as audit


# ─── Writes ─────────────────────────────────────────────────


class TestRecord:
    async def test_inserts_and_returns_rowid(self, temp_db):
        db = await get_db()
        try:
            rid = await audit.record(
                db,
                action=audit.ACTION_UPLOAD,
                trigger=audit.TRIGGER_SCHEDULED,
                outcome=audit.OUTCOME_SUCCESS,
                mode="ratio",
                amount="50",
                tier="trigger:ratio",
                cost_points=25_000,
                user_bonus_after=75_000.5,
            )
            assert rid > 0
        finally:
            await db.close()

    async def test_minimal_row_with_only_required_fields(self, temp_db):
        # action, trigger, outcome are the only NOT NULL columns.
        db = await get_db()
        try:
            rid = await audit.record(
                db,
                action=audit.ACTION_VIP,
                trigger=audit.TRIGGER_SCHEDULED,
                outcome=audit.OUTCOME_SKIP_DISABLED,
            )
            rows = await audit.list_recent(db)
            row = next(r for r in rows if r.id == rid)
            assert row.mode is None
            assert row.amount is None
            assert row.cost_points is None
        finally:
            await db.close()

    async def test_skip_row_persists_reason_in_outcome(self, temp_db):
        db = await get_db()
        try:
            rid = await audit.record(
                db,
                action=audit.ACTION_UPLOAD,
                trigger=audit.TRIGGER_SCHEDULED,
                outcome=audit.OUTCOME_SKIP_NO_TRIGGER,
            )
            rows = await audit.list_recent(db)
            row = next(r for r in rows if r.id == rid)
            assert row.outcome == "skip_no_trigger"
        finally:
            await db.close()

    async def test_buffer_gate_block_row(self, temp_db):
        # Dispatcher writes this shape when buffer gate skips a grab.
        db = await get_db()
        try:
            await audit.record(
                db,
                action=audit.ACTION_BUFFER_GATE_BLOCK,
                trigger=audit.TRIGGER_IRC_AUTOGRAB,
                outcome=audit.OUTCOME_BUFFER_GATE_BLOCK,
                torrent_id="965093",
                message="Need 2.5 GB more buffer",
            )
            rows = await audit.list_recent(
                db, action=audit.ACTION_BUFFER_GATE_BLOCK,
            )
            assert len(rows) == 1
            assert rows[0].torrent_id == "965093"
            assert rows[0].trigger == audit.TRIGGER_IRC_AUTOGRAB
        finally:
            await db.close()


# ─── Reads ──────────────────────────────────────────────────


class TestListRecent:
    async def _populate(self, db):
        # Three rows in a deterministic order.
        await audit.record(
            db, action=audit.ACTION_VIP, trigger=audit.TRIGGER_SCHEDULED,
            outcome=audit.OUTCOME_SKIP_BELOW_INTERVAL,
        )
        await audit.record(
            db, action=audit.ACTION_UPLOAD, trigger=audit.TRIGGER_SCHEDULED,
            outcome=audit.OUTCOME_SUCCESS, tier="trigger:ratio", amount="50",
        )
        await audit.record(
            db, action=audit.ACTION_PERSONAL_FL, trigger=audit.TRIGGER_USER_GRAB,
            outcome=audit.OUTCOME_SUCCESS, torrent_id="12345",
            cost_points=50_000, user_bonus_after=30_000.0,
        )

    async def test_returns_most_recent_first(self, temp_db):
        db = await get_db()
        try:
            await self._populate(db)
            rows = await audit.list_recent(db)
            assert len(rows) == 3
            # Most recent insertion is the personal_fl one.
            assert rows[0].action == audit.ACTION_PERSONAL_FL
            assert rows[-1].action == audit.ACTION_VIP
        finally:
            await db.close()

    async def test_honors_limit(self, temp_db):
        db = await get_db()
        try:
            await self._populate(db)
            rows = await audit.list_recent(db, limit=2)
            assert len(rows) == 2
        finally:
            await db.close()

    async def test_filters_by_action(self, temp_db):
        db = await get_db()
        try:
            await self._populate(db)
            upload_rows = await audit.list_recent(db, action=audit.ACTION_UPLOAD)
            assert len(upload_rows) == 1
            assert upload_rows[0].tier == "trigger:ratio"
        finally:
            await db.close()

    async def test_empty_table_returns_empty_list(self, temp_db):
        db = await get_db()
        try:
            assert await audit.list_recent(db) == []
        finally:
            await db.close()


class TestLatestSuccess:
    async def test_returns_most_recent_success_of_type(self, temp_db):
        db = await get_db()
        try:
            # One upload failure, then a success, then a VIP success.
            # Only the upload success should come back for action=upload.
            await audit.record(
                db, action=audit.ACTION_UPLOAD, trigger=audit.TRIGGER_SCHEDULED,
                outcome=audit.OUTCOME_FAILURE, tier="trigger:ratio",
                message="Not enough bonus, s1",
            )
            await audit.record(
                db, action=audit.ACTION_UPLOAD, trigger=audit.TRIGGER_MANUAL,
                outcome=audit.OUTCOME_SUCCESS, amount="20",
                user_bonus_after=5_000.0,
            )
            await audit.record(
                db, action=audit.ACTION_VIP, trigger=audit.TRIGGER_SCHEDULED,
                outcome=audit.OUTCOME_SUCCESS, amount="4",
            )

            latest = await audit.latest_success(db, action=audit.ACTION_UPLOAD)
            assert latest is not None
            assert latest.action == audit.ACTION_UPLOAD
            assert latest.outcome == audit.OUTCOME_SUCCESS
            assert latest.amount == "20"
        finally:
            await db.close()

    async def test_none_when_no_successes(self, temp_db):
        db = await get_db()
        try:
            await audit.record(
                db, action=audit.ACTION_VIP, trigger=audit.TRIGGER_SCHEDULED,
                outcome=audit.OUTCOME_SKIP_DISABLED,
            )
            assert await audit.latest_success(db, action=audit.ACTION_VIP) is None
        finally:
            await db.close()


# ─── Schema / migration ─────────────────────────────────────


class TestSchema:
    async def test_table_exists_after_init(self, temp_db):
        # The temp_db fixture runs init_db(), which applies SCHEMA.
        # Confirm the table + indexes are actually there.
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE (name = 'economy_audit' OR name LIKE 'idx_economy_audit_%')"
            )
            names = {row[0] for row in await cursor.fetchall()}
        finally:
            await db.close()
        assert "economy_audit" in names
        assert "idx_economy_audit_occurred" in names
        assert "idx_economy_audit_action" in names

    async def test_occurred_at_auto_populates(self, temp_db):
        db = await get_db()
        try:
            rid = await audit.record(
                db, action=audit.ACTION_VIP, trigger=audit.TRIGGER_SCHEDULED,
                outcome=audit.OUTCOME_SUCCESS,
            )
            rows = await audit.list_recent(db)
            row = next(r for r in rows if r.id == rid)
            # SQLite DEFAULT (datetime('now')) gives a "YYYY-MM-DD HH:MM:SS" string
            assert len(row.occurred_at) >= 19
            assert row.occurred_at[4] == "-"
            assert row.occurred_at[10] == " "
        finally:
            await db.close()


# ─── Constant vocabulary smoke tests ────────────────────────


class TestConstants:
    def test_actions_are_distinct(self):
        actions = {
            audit.ACTION_VIP,
            audit.ACTION_UPLOAD,
            audit.ACTION_PERSONAL_FL,
            audit.ACTION_BUFFER_GATE_BLOCK,
        }
        assert len(actions) == 4

    def test_skip_outcomes_have_skip_prefix(self):
        # The UI filters skips by `outcome LIKE 'skip_%'` — guard
        # against accidentally dropping the prefix.
        for value in (
            audit.OUTCOME_SKIP_DISABLED,
            audit.OUTCOME_SKIP_BELOW_INTERVAL,
            audit.OUTCOME_SKIP_NO_TRIGGER,
            audit.OUTCOME_SKIP_INSUFFICIENT_BONUS,
        ):
            assert value.startswith("skip_")

    def test_success_and_failure_are_distinct_from_skips(self):
        assert not audit.OUTCOME_SUCCESS.startswith("skip_")
        assert not audit.OUTCOME_FAILURE.startswith("skip_")
