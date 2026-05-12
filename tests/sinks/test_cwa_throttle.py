"""
Unit tests for the per-ingest-path CWA throttle.

The throttle exists to work around a CWA cps wedge when overlapping
imports trigger the post-import duplicate scan — see
`app/sinks/_cwa_throttle.py` for the failure-mode write-up.
"""
import asyncio
import time
from pathlib import Path

import pytest

from app.metadata.extract import BookMetadata
from app.sinks import _cwa_throttle
from app.sinks._cwa_throttle import throttle
from app.sinks.cwa import CWASink


@pytest.fixture(autouse=True)
def _reset_throttle_state():
    """Throttle slots persist at module level. Reset between tests so
    leftover timestamps and locks-bound-to-closed-loops don't bleed."""
    _cwa_throttle._reset_for_tests()
    yield
    _cwa_throttle._reset_for_tests()


class TestThrottleContextManager:
    async def test_first_delivery_does_not_wait(self):
        # No prior release on this slot — should pass through immediately.
        t0 = time.monotonic()
        async with throttle("/some/path", min_gap_seconds=5.0):
            pass
        assert time.monotonic() - t0 < 0.1

    async def test_second_delivery_within_window_waits(self):
        # First delivery primes the slot; second within gap waits.
        async with throttle("/p", min_gap_seconds=0.2):
            pass
        t0 = time.monotonic()
        async with throttle("/p", min_gap_seconds=0.2):
            pass
        elapsed = time.monotonic() - t0
        # Second should wait ≈ gap (with a small tolerance for sleep
        # precision). Lower bound dominant — upper bound generous.
        assert 0.15 <= elapsed <= 0.5

    async def test_partial_elapsed_subtracts_from_wait(self):
        async with throttle("/p", min_gap_seconds=0.3):
            pass
        # Sleep half the gap outside the throttle.
        await asyncio.sleep(0.15)
        t0 = time.monotonic()
        async with throttle("/p", min_gap_seconds=0.3):
            pass
        elapsed = time.monotonic() - t0
        # Remaining wait ≈ gap - 0.15 = 0.15s.
        assert 0.10 <= elapsed <= 0.35

    async def test_elapsed_past_gap_no_wait(self):
        async with throttle("/p", min_gap_seconds=0.1):
            pass
        await asyncio.sleep(0.15)  # well past the gap
        t0 = time.monotonic()
        async with throttle("/p", min_gap_seconds=0.1):
            pass
        assert time.monotonic() - t0 < 0.05

    async def test_different_paths_are_independent(self):
        # Prime slot A, then immediately deliver to B — no wait.
        async with throttle("/a", min_gap_seconds=5.0):
            pass
        t0 = time.monotonic()
        async with throttle("/b", min_gap_seconds=5.0):
            pass
        assert time.monotonic() - t0 < 0.05

    async def test_gap_zero_disables_throttle(self):
        # gap=0 should bypass the slot entirely — no lock acquired,
        # no timestamp recorded.
        async with throttle("/p", min_gap_seconds=0):
            pass
        assert "/p" not in _cwa_throttle._state
        # And consecutive zero-gap deliveries don't wait either.
        t0 = time.monotonic()
        for _ in range(3):
            async with throttle("/p", min_gap_seconds=0):
                pass
        assert time.monotonic() - t0 < 0.05

    async def test_negative_gap_treated_as_disabled(self):
        async with throttle("/p", min_gap_seconds=-1.0):
            pass
        assert "/p" not in _cwa_throttle._state

    async def test_serializes_concurrent_deliveries(self):
        # Two coroutines fire at the same time; lock serializes them
        # AND the second pays the gap. Total wall clock ≥ 1× gap.
        async def deliver():
            async with throttle("/shared", min_gap_seconds=0.2):
                pass

        t0 = time.monotonic()
        await asyncio.gather(deliver(), deliver())
        elapsed = time.monotonic() - t0
        # First completes immediately, second waits ≈ gap. Lower bound
        # ≥ gap; upper bound looser to absorb scheduling jitter.
        assert 0.15 <= elapsed <= 0.5


class TestCWASinkWithThrottle:
    """Integration: CWASink with a non-zero min_gap_seconds enforces
    the throttle on real consecutive deliveries."""

    async def test_two_deliveries_pay_gap(self, tmp_path: Path):
        src1 = tmp_path / "a.epub"
        src2 = tmp_path / "b.epub"
        src1.write_bytes(b"a")
        src2.write_bytes(b"b")
        ingest = tmp_path / "ingest"
        sink = CWASink(str(ingest), min_gap_seconds=0.2)

        t0 = time.monotonic()
        r1 = await sink.deliver(str(src1), BookMetadata())
        r2 = await sink.deliver(str(src2), BookMetadata())
        elapsed = time.monotonic() - t0

        assert r1.success and r2.success
        assert (ingest / "a.epub").exists()
        assert (ingest / "b.epub").exists()
        # Second delivery pays ≈ gap — total run ≥ gap, well under 1s.
        assert 0.15 <= elapsed <= 0.6

    async def test_default_zero_gap_does_not_wait(self, tmp_path: Path):
        # Constructing CWASink with min_gap_seconds=0 disables the
        # throttle — useful for operators who've turned off CWA's
        # auto-duplicate-scan and don't need the protection.
        src1 = tmp_path / "a.epub"
        src2 = tmp_path / "b.epub"
        src1.write_bytes(b"a")
        src2.write_bytes(b"b")
        sink = CWASink(str(tmp_path / "ingest"), min_gap_seconds=0)

        t0 = time.monotonic()
        await sink.deliver(str(src1), BookMetadata())
        await sink.deliver(str(src2), BookMetadata())
        # Both deliveries are pure file copies — should be near-instant.
        assert time.monotonic() - t0 < 0.1
