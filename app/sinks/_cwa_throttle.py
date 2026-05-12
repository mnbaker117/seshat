"""
Per-ingest-path inter-book throttle for the CWA sink.

CWA's post-import duplicate scan runs inside the single-threaded cps
web process on a 5s debounce. When Seshat drops a second book into
the watched ingest folder while that scan is still pending or running,
the second book's ingest-processor → cps web-API callbacks
(`session_refresh`, `cache_invalidate`, `schedule_scan`) all hit a
5s read timeout and cps loses its HTTP listener entirely until the
container is restarted (process stays alive, GIL never releases).

Reproduced 2026-05-11 ~22:40 with a two-book approve-all from Seshat.

This module enforces a minimum gap between successive deliveries to
the same ingest path. The gap-since-last model auto-handles every
caller pattern: a single book pays no wait, a bulk burst pays
`(N-1) * gap`, and parallel deliveries to different CWA targets are
independent (multi-library setups don't contend).
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

_log = logging.getLogger("seshat.sinks.cwa")


@dataclass
class _Slot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_release_ts: float = 0.0  # monotonic seconds; 0 = never


_state: dict[str, _Slot] = {}


def _slot_for(ingest_path: str) -> _Slot:
    slot = _state.get(ingest_path)
    if slot is None:
        slot = _Slot()
        _state[ingest_path] = slot
    return slot


@asynccontextmanager
async def throttle(
    ingest_path: str,
    min_gap_seconds: float,
) -> AsyncIterator[None]:
    """Serialize and rate-limit deliveries to a single CWA ingest path.

    Acquires a per-path lock, sleeps long enough that this delivery
    starts at least `min_gap_seconds` after the previous one's
    completion, yields, then records the completion time on exit.

    With `min_gap_seconds <= 0` the throttle is a no-op pass-through:
    no lock contention, no sleep, no timestamp update. Useful for
    operators who've disabled CWA's auto-duplicate-scan feature on
    their side and don't need the protection.
    """
    if min_gap_seconds <= 0:
        yield
        return

    slot = _slot_for(ingest_path)
    async with slot.lock:
        elapsed = time.monotonic() - slot.last_release_ts
        wait = min_gap_seconds - elapsed
        if wait > 0 and slot.last_release_ts > 0:
            _log.info(
                "cwa throttle: delaying delivery to %s by %.1fs (gap=%.1fs)",
                ingest_path, wait, min_gap_seconds,
            )
            await asyncio.sleep(wait)
        try:
            yield
        finally:
            slot.last_release_ts = time.monotonic()


def _reset_for_tests() -> None:
    """Drop all throttle state. Tests call this between cases to avoid
    leakage and to release locks bound to a closed event loop."""
    _state.clear()
