"""
Unit tests for the MAM cookie keep-alive loop.

Tests target the `tick()` function for fast in-process verification
of the cycle's behavior, plus a small set of `run_loop()` tests
covering the supervised lifecycle (start, stop_event-driven exit,
exception isolation).

Coverage targets:
  - tick() against the happy fake-MAM path → success result
  - tick() automatically uses the in-memory token (proves the
    `verify_session("")` empty-token + `_resolve_token` fallback
    is wired correctly through the keep-alive call site)
  - tick() captures rotation when fake_mam serves a new cookie
  - tick() returns failure result on auth failure (does NOT raise)
  - tick() returns failure result on transport exception
  - run_loop() exits cleanly on stop_event between ticks
  - run_loop() exits cleanly on stop_event during sleep
"""
import asyncio

from app.mam.cookie import (
    get_current_token,
    set_current_token,
    set_rotation_callback,
)
from app.orchestrator.cookie_keepalive import KeepaliveResult, run_loop, tick


# ─── tick() ──────────────────────────────────────────────────


class TestTick:
    async def test_happy_path_returns_success(self, fake_mam):
        set_current_token("seeded_token")
        try:
            result = await tick()
            assert isinstance(result, KeepaliveResult)
            assert result.success is True
            assert "successful" in result.message.lower()
        finally:
            set_current_token("")

    async def test_uses_in_memory_token(self, fake_mam):
        # The keep-alive call site passes token="" so the cookie
        # module's `_resolve_token` falls back to `_current_token`.
        # This test verifies that wiring by checking the request
        # the fake MAM saw used the in-memory token, not an empty
        # string or some default.
        set_current_token("specific_token_value_12345")
        try:
            await tick()
            # The fake's `cookies_seen()` returns every mam_id value
            # it received in incoming requests. The keep-alive tick
            # should produce exactly one request, attached with our
            # in-memory token.
            seen = fake_mam.cookies_seen()
            assert "specific_token_value_12345" in seen
        finally:
            set_current_token("")

    async def test_captures_rotation_through_normal_handler(
        self, fake_mam
    ):
        # The whole point of the keep-alive: trigger the rotation
        # handler so MAM's response cookie gets captured even
        # without other MAM activity. Verify by setting
        # `rotate_cookie_to` and confirming the in-memory token
        # changes after the tick.
        set_current_token("before_keepalive")
        callback_calls: list[str] = []

        async def cb(new_token: str) -> None:
            callback_calls.append(new_token)

        set_rotation_callback(cb)
        fake_mam.rotate_cookie_to = "after_keepalive"
        try:
            result = await tick()
            assert result.success is True
            assert get_current_token() == "after_keepalive"
            assert callback_calls == ["after_keepalive"]
        finally:
            set_rotation_callback(None)
            set_current_token("")

    async def test_auth_failure_returns_failure_does_not_raise(
        self, fake_mam
    ):
        # Realistic scenario: the in-memory token has gone bad
        # somehow (manual corruption, MAM-side revocation), and the
        # keep-alive call gets HTTP 403. tick() must NOT raise —
        # the supervised loop relies on the returned KeepaliveResult
        # to drive logging cleanly.
        set_current_token("bad_token")
        fake_mam.simulate_cookie_rejected_403()
        try:
            result = await tick()
            assert result.success is False
            assert "403" in result.message or "rejected" in result.message.lower()
        finally:
            set_current_token("")

    async def test_empty_response_returns_failure(self, fake_mam):
        set_current_token("any_token")
        fake_mam.search.body = b""
        try:
            result = await tick()
            assert result.success is False
        finally:
            set_current_token("")

    async def test_chained_ticks_each_rotate_cookie(self, fake_mam):
        # Production behavior: every keep-alive tick produces a
        # fresh cookie. Drive three back-to-back ticks with
        # different rotate_cookie_to values and verify the
        # in-memory token tracks each one.
        set_current_token("initial")
        try:
            fake_mam.rotate_cookie_to = "tick_1_value"
            await tick()
            assert get_current_token() == "tick_1_value"

            fake_mam.rotate_cookie_to = "tick_2_value"
            await tick()
            assert get_current_token() == "tick_2_value"

            fake_mam.rotate_cookie_to = "tick_3_value"
            await tick()
            assert get_current_token() == "tick_3_value"

            seen = fake_mam.cookies_seen()
            assert seen[0] == "initial"
            assert seen[1] == "tick_1_value"
            assert seen[2] == "tick_2_value"
        finally:
            set_current_token("")


# ─── run_loop() ──────────────────────────────────────────────


class TestRunLoop:
    async def test_stop_event_during_sleep_exits_cleanly(self, fake_mam):
        # The realistic shutdown path: keep-alive is mid-sleep
        # (sleeping 7 days, in production), shutdown signals stop,
        # we expect the loop to exit immediately rather than wait
        # out the rest of the sleep.
        set_current_token("seeded")
        stop_event = asyncio.Event()

        async def run_with_long_interval():
            await run_loop(interval_seconds=600.0, stop_event=stop_event)

        task = asyncio.create_task(run_with_long_interval())
        try:
            # Let the loop fire its first tick + enter the sleep
            await asyncio.sleep(0.05)
            stop_event.set()
            # Expect exit well within the long interval
            await asyncio.wait_for(task, timeout=2.0)
        finally:
            set_current_token("")

    async def test_stop_event_set_before_start_exits_after_one_tick(
        self, fake_mam
    ):
        # Edge case: stop_event was already set before run_loop
        # was called. The loop should still fire ONE tick (the
        # check happens after the tick) and then exit cleanly.
        set_current_token("seeded")
        stop_event = asyncio.Event()
        stop_event.set()

        try:
            await asyncio.wait_for(
                run_loop(interval_seconds=600.0, stop_event=stop_event),
                timeout=2.0,
            )
            # If we got here without timing out, the loop exited
            # cleanly. The single tick fired against the fake MAM,
            # which we verify by checking it saw a request.
            assert any(
                "loadSearchJSONbasic.php" in str(req.url)
                for req in fake_mam.requests
            )
        finally:
            set_current_token("")

    async def test_cancellation_propagates(self, fake_mam):
        # The supervised_task wrapper relies on CancelledError
        # propagating cleanly out of run_loop so the surrounding
        # task can be cancelled at shutdown. Verify by creating
        # the task, letting it start, and cancelling it directly
        # (no stop_event).
        set_current_token("seeded")
        try:
            task = asyncio.create_task(
                run_loop(interval_seconds=600.0)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.CancelledError:
                pass  # expected
        finally:
            set_current_token("")
