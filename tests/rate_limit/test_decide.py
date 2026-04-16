"""
Unit tests for `decide_grab_action` — the pure decision function
the IRC dispatcher and the inject endpoint both consult to figure
out what to do with an incoming announce.

No DB, no fixtures — just exercise the decision matrix exhaustively.
"""
from app.rate_limit import decide_grab_action


class TestDecideGrabAction:
    def test_budget_available_submits(self):
        decision = decide_grab_action(
            budget_used=10,
            budget_cap=200,
            queue_size=0,
            queue_max=100,
            queue_mode_enabled=True,
        )
        assert decision.action == "submit"
        assert decision.reason == "budget_available"

    def test_budget_exact_full_queues(self):
        # Boundary: used == cap is "full" not "available".
        decision = decide_grab_action(
            budget_used=200,
            budget_cap=200,
            queue_size=0,
            queue_max=100,
            queue_mode_enabled=True,
        )
        assert decision.action == "queue"

    def test_budget_full_drop_mode_drops(self):
        decision = decide_grab_action(
            budget_used=200,
            budget_cap=200,
            queue_size=0,
            queue_max=100,
            queue_mode_enabled=False,
        )
        assert decision.action == "drop"
        assert decision.reason == "budget_full_drop_mode"

    def test_budget_full_queue_full_drops(self):
        decision = decide_grab_action(
            budget_used=200,
            budget_cap=200,
            queue_size=100,
            queue_max=100,
            queue_mode_enabled=True,
        )
        assert decision.action == "drop"
        assert decision.reason == "budget_full_queue_full"

    def test_budget_full_queue_has_room_queues(self):
        decision = decide_grab_action(
            budget_used=200,
            budget_cap=200,
            queue_size=99,
            queue_max=100,
            queue_mode_enabled=True,
        )
        assert decision.action == "queue"
        assert decision.reason == "budget_full_queueing"

    def test_decision_includes_counters_for_logging(self):
        # The decision is consumed for both action AND audit logging,
        # so it must echo back the counters that fed into it.
        decision = decide_grab_action(
            budget_used=42,
            budget_cap=200,
            queue_size=7,
            queue_max=100,
            queue_mode_enabled=True,
        )
        assert decision.budget_used == 42
        assert decision.budget_cap == 200
        assert decision.queue_size == 7
        assert decision.queue_max == 100

    def test_zero_cap_always_drops_or_queues(self):
        # Defensive: a misconfigured zero cap should at least not crash.
        decision = decide_grab_action(
            budget_used=0,
            budget_cap=0,
            queue_size=0,
            queue_max=100,
            queue_mode_enabled=True,
        )
        assert decision.action == "queue"

    def test_zero_cap_drop_mode(self):
        decision = decide_grab_action(
            budget_used=0,
            budget_cap=0,
            queue_size=0,
            queue_max=100,
            queue_mode_enabled=False,
        )
        assert decision.action == "drop"
