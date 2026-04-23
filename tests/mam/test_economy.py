"""
Unit tests for the MAM economy decision engine.

Pure logic, no I/O. Every test constructs a UserStatus + config and
asserts on the returned EconomyDecision. Exhaustive because the
scheduler spends real bonus points based on these decisions and a
regression is silent and expensive.
"""
from __future__ import annotations

import pytest

from app.mam.bonus_buy import BP_PER_UPLOAD_GB, BP_PER_VIP_WEEK
from app.mam.economy import (
    EconomyDecision,
    UploadBuyConfig,
    VipBuyConfig,
    decide_upload_buy,
    decide_vip_buy,
    estimate_upload_cost_bp,
    max_affordable_upload_gb,
)
from app.mam.user_status import UserStatus


def _status(
    *,
    ratio: float = 2.0,
    seedbonus: float = 100_000.0,
    upload_buffer_bytes: int = 20_000_000_000,  # 20 GB
    wedges: int = 5,
    uploaded_bytes: int = 1_000_000_000_000,
    downloaded_bytes: int = 500_000_000_000,
) -> UserStatus:
    return UserStatus(
        ratio=ratio,
        wedges=wedges,
        seedbonus=seedbonus,
        classname="Power User",
        username="tester",
        uid=1,
        uploaded_bytes=uploaded_bytes,
        downloaded_bytes=downloaded_bytes,
        upload_buffer_bytes=upload_buffer_bytes,
    )


# ─── VIP decisions ──────────────────────────────────────────


class TestVipDisabled:
    def test_disabled_skips(self):
        d = decide_vip_buy(
            _status(),
            VipBuyConfig(enabled=False),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "skip"
        assert d.reason == "disabled"


class TestVipIntervalGate:
    def test_never_bought_fires_immediately(self):
        d = decide_vip_buy(
            _status(),
            VipBuyConfig(enabled=True, interval_hours=24),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "buy"
        assert d.reason == "trigger:interval"

    def test_too_soon_skips(self):
        now = 1_000_000
        # Last buy 1h ago, interval is 24h
        d = decide_vip_buy(
            _status(),
            VipBuyConfig(enabled=True, interval_hours=24),
            last_bought_at=now - 3600, now_ts=now,
        )
        assert d.action == "skip"
        assert d.reason == "below_interval"

    def test_exactly_at_interval_fires(self):
        now = 1_000_000
        d = decide_vip_buy(
            _status(),
            VipBuyConfig(enabled=True, interval_hours=24),
            last_bought_at=now - 24 * 3600, now_ts=now,
        )
        assert d.action == "buy"


class TestVipMinBonusFloor:
    def test_below_floor_skips(self):
        d = decide_vip_buy(
            _status(seedbonus=1000),
            VipBuyConfig(enabled=True, min_bonus=5000),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "skip"
        assert d.reason == "insufficient_bonus"

    def test_zero_floor_does_not_gate(self):
        d = decide_vip_buy(
            _status(seedbonus=10),
            VipBuyConfig(enabled=True, min_bonus=0, weeks="max"),
            last_bought_at=0, now_ts=1_000_000,
        )
        # Even with 10 BP, "max" lets MAM decide — we don't block.
        assert d.action == "buy"


class TestVipAffordabilityCheck:
    def test_numeric_weeks_cost_checked(self):
        # 4 weeks × 1250 = 5000 BP; balance 4000 → insufficient.
        d = decide_vip_buy(
            _status(seedbonus=4000),
            VipBuyConfig(enabled=True, weeks=4),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "skip"
        assert d.reason == "insufficient_bonus"
        assert d.estimated_cost_bp == 4 * BP_PER_VIP_WEEK

    def test_numeric_weeks_balance_covers_cost(self):
        d = decide_vip_buy(
            _status(seedbonus=10_000),
            VipBuyConfig(enabled=True, weeks=4),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "buy"
        assert d.weeks == 4
        assert d.estimated_cost_bp == 4 * BP_PER_VIP_WEEK

    def test_max_weeks_defers_cost_to_mam(self):
        # "max" lets MAM decide the credit amount — no pre-check.
        d = decide_vip_buy(
            _status(seedbonus=100),
            VipBuyConfig(enabled=True, weeks="max"),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "buy"
        assert d.weeks == "max"
        assert d.estimated_cost_bp is None


# ─── Upload decisions ──────────────────────────────────────


class TestUploadDisabled:
    def test_disabled_skips(self):
        d = decide_upload_buy(
            _status(),
            UploadBuyConfig(enabled=False),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.reason == "disabled"


class TestUploadIntervalGate:
    def test_never_bought_fires_when_trigger_matches(self):
        d = decide_upload_buy(
            _status(ratio=1.0),
            UploadBuyConfig(
                enabled=True, interval_hours=6,
                ratio_trigger=True, ratio_floor=1.5,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "buy"

    def test_too_soon_skips_before_trigger_check(self):
        now = 1_000_000
        d = decide_upload_buy(
            _status(ratio=1.0),  # would trigger, but interval blocks
            UploadBuyConfig(
                enabled=True, interval_hours=6,
                ratio_trigger=True, ratio_floor=1.5,
            ),
            last_bought_at=now - 60, now_ts=now,
        )
        assert d.action == "skip"
        assert d.reason == "below_interval"


class TestUploadRatioTrigger:
    def test_fires_below_floor(self):
        d = decide_upload_buy(
            _status(ratio=1.2),
            UploadBuyConfig(
                enabled=True, ratio_trigger=True,
                ratio_floor=1.5, ratio_chunk_gb=50,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "buy"
        assert d.mode == "ratio"
        assert d.reason == "trigger:ratio"
        assert d.amount_gb == 50

    def test_does_not_fire_at_floor(self):
        d = decide_upload_buy(
            _status(ratio=1.5),
            UploadBuyConfig(
                enabled=True, ratio_trigger=True, ratio_floor=1.5,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "skip"
        assert d.reason == "no_trigger"

    def test_trigger_disabled_does_not_fire(self):
        d = decide_upload_buy(
            _status(ratio=0.1),
            UploadBuyConfig(
                enabled=True, ratio_trigger=False, ratio_floor=1.5,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "skip"
        assert d.reason == "no_trigger"


class TestUploadBufferTrigger:
    def test_fires_below_floor_gb(self):
        # 5 GB buffer, floor is 10 GB
        d = decide_upload_buy(
            _status(upload_buffer_bytes=5_000_000_000),
            UploadBuyConfig(
                enabled=True, buffer_trigger=True,
                buffer_floor_gb=10, buffer_chunk_gb=50,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "buy"
        assert d.mode == "buffer"
        assert d.amount_gb == 50

    def test_does_not_fire_above_floor(self):
        d = decide_upload_buy(
            _status(upload_buffer_bytes=15_000_000_000),
            UploadBuyConfig(
                enabled=True, buffer_trigger=True, buffer_floor_gb=10,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "skip"


class TestUploadBonusTrigger:
    def test_fires_above_ceiling(self):
        # 10100 seedbonus, ceiling 5000 → excess 5100 / 500 = 10.2 GB
        d = decide_upload_buy(
            _status(seedbonus=10_100),
            UploadBuyConfig(
                enabled=True, bonus_trigger=True, bonus_ceiling=5000,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "buy"
        assert d.mode == "bonus"
        assert d.reason == "trigger:bonus"
        assert d.amount_gb == pytest.approx(10.2)
        assert d.estimated_cost_bp == 5100

    def test_spend_drops_balance_to_ceiling(self):
        # Post-buy: seedbonus − cost = ceiling (the design invariant).
        d = decide_upload_buy(
            _status(seedbonus=12_345),
            UploadBuyConfig(
                enabled=True, bonus_trigger=True, bonus_ceiling=5000,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        post = 12_345 - d.estimated_cost_bp
        assert post == 5000

    def test_at_ceiling_does_not_fire(self):
        d = decide_upload_buy(
            _status(seedbonus=5000),
            UploadBuyConfig(
                enabled=True, bonus_trigger=True, bonus_ceiling=5000,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "skip"
        assert d.reason == "no_trigger"

    def test_tiny_excess_does_not_rattle_audit(self):
        # Ceiling 5000, balance 5005 → excess 5 / 500 = 0.01 GB. We
        # explicitly skip amounts under 0.1 GB because spending 5 BP
        # for an audit row is worse than just waiting.
        d = decide_upload_buy(
            _status(seedbonus=5005),
            UploadBuyConfig(
                enabled=True, bonus_trigger=True, bonus_ceiling=5000,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "skip"
        assert d.reason == "no_trigger"


class TestUploadTriggerPriority:
    def test_ratio_beats_buffer_and_bonus(self):
        d = decide_upload_buy(
            _status(ratio=1.0, upload_buffer_bytes=1_000_000_000, seedbonus=10_000),
            UploadBuyConfig(
                enabled=True,
                ratio_trigger=True, ratio_floor=1.5, ratio_chunk_gb=10,
                buffer_trigger=True, buffer_floor_gb=10, buffer_chunk_gb=20,
                bonus_trigger=True, bonus_ceiling=1000,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.mode == "ratio"
        assert d.amount_gb == 10

    def test_buffer_beats_bonus_when_ratio_ok(self):
        d = decide_upload_buy(
            _status(ratio=5.0, upload_buffer_bytes=1_000_000_000, seedbonus=10_000),
            UploadBuyConfig(
                enabled=True,
                ratio_trigger=True, ratio_floor=1.5,
                buffer_trigger=True, buffer_floor_gb=10, buffer_chunk_gb=20,
                bonus_trigger=True, bonus_ceiling=1000,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.mode == "buffer"
        assert d.amount_gb == 20

    def test_bonus_fires_when_ratio_and_buffer_ok(self):
        d = decide_upload_buy(
            _status(ratio=5.0, upload_buffer_bytes=50_000_000_000, seedbonus=10_000),
            UploadBuyConfig(
                enabled=True,
                ratio_trigger=True, ratio_floor=1.5,
                buffer_trigger=True, buffer_floor_gb=10,
                bonus_trigger=True, bonus_ceiling=1000,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.mode == "bonus"


class TestUploadAffordability:
    def test_ratio_trigger_cannot_afford_skips(self):
        # 50 GB costs 25000 BP; balance 10000 → skip.
        d = decide_upload_buy(
            _status(ratio=1.0, seedbonus=10_000),
            UploadBuyConfig(
                enabled=True, ratio_trigger=True,
                ratio_floor=1.5, ratio_chunk_gb=50,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "skip"
        assert d.reason == "insufficient_bonus"
        assert d.mode == "ratio"
        assert d.amount_gb == 50
        assert d.estimated_cost_bp == 25_000

    def test_bonus_trigger_cannot_underrun_balance(self):
        # Bonus mode's formula guarantees seedbonus >= cost — there
        # is no scenario where affordability fails in bonus mode.
        d = decide_upload_buy(
            _status(seedbonus=5_500),
            UploadBuyConfig(
                enabled=True, bonus_trigger=True, bonus_ceiling=5000,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.action == "buy"


# ─── Cost helpers ───────────────────────────────────────────


class TestCostHelpers:
    def test_estimate_upload_cost_linear(self):
        assert estimate_upload_cost_bp(10) == 10 * BP_PER_UPLOAD_GB
        assert estimate_upload_cost_bp(2.5) == int(2.5 * BP_PER_UPLOAD_GB)

    def test_estimate_upload_cost_rounds_half(self):
        # 0.001 GB × 500 = 0.5 BP → rounds to 0 (banker's) or 1
        # (traditional); our code uses round() which on Python is
        # banker's. Either way, the production path never calls
        # estimate with such tiny amounts.
        assert estimate_upload_cost_bp(0.001) in (0, 1)

    def test_max_affordable_floors_to_whole_gb(self):
        assert max_affordable_upload_gb(9_992) == 19  # 9992 // 500 = 19
        assert max_affordable_upload_gb(10_000) == 20
        assert max_affordable_upload_gb(499) == 0

    def test_max_affordable_on_zero_or_negative(self):
        assert max_affordable_upload_gb(0) == 0
        assert max_affordable_upload_gb(-100) == 0


# ─── Decision shape contract ────────────────────────────────


class TestDecisionShape:
    def test_frozen_dataclass_cannot_mutate(self):
        d = EconomyDecision(action="skip", reason="disabled")
        with pytest.raises(Exception):
            d.action = "buy"  # type: ignore[misc]

    def test_buy_decision_carries_mode_for_audit(self):
        # The scheduler writes `mode` to the economy_audit table —
        # confirm decide_upload_buy always sets it on `buy` outcomes.
        d = decide_upload_buy(
            _status(ratio=1.0),
            UploadBuyConfig(
                enabled=True, ratio_trigger=True,
                ratio_floor=1.5, ratio_chunk_gb=5,
            ),
            last_bought_at=0, now_ts=1_000_000,
        )
        assert d.mode is not None
        assert d.action == "buy"
