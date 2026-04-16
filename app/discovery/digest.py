"""
Digest scheduler — consolidates queued notification events and sends
them on a daily/weekly cadence via ntfy.

When the user enables ntfy_digest_enabled in Settings, all event-specific
notifiers in app.notify enqueue their events into an in-memory queue
instead of sending immediately. This module is responsible for draining
that queue on schedule and emitting one consolidated notification.

Schedule:
  - daily   → flush at 09:00 local each day
  - weekly  → flush at 09:00 local on Monday

Persistence: the queue is in-memory only. A container restart loses any
events that haven't flushed yet — acceptable for v1.1 (events are
informational, not actionable). If we ever wire it to persist, the
on-disk format should be JSONL appended-to per event.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from app.config import load_settings
from app.discovery.notify import DigestEvent, drain_digest, send

logger = logging.getLogger("seshat.discovery.digest")

_KIND_LABELS = {
    "scan_complete": "Source scans",
    "new_books":     "New books discovered",
    "mam":           "MAM scans",
    "hermeece":      "Sent to Hermeece",
    "library":       "Library syncs",
    "cookie":        "MAM cookie rotations",
}


def _format_digest(events: list[DigestEvent], schedule: str) -> tuple[str, str]:
    """Return (title, message) for a consolidated digest of `events`."""
    counts = Counter(e.kind for e in events)
    title = f"AthenaScout {schedule} digest — {len(events)} event(s)"

    lines: list[str] = []
    # Section per kind in a stable preferred order
    for kind in ("scan_complete", "new_books", "mam", "hermeece", "library", "cookie"):
        kind_events = [e for e in events if e.kind == kind]
        if not kind_events:
            continue
        label = _KIND_LABELS.get(kind, kind)
        lines.append(f"━━ {label} ({counts[kind]}) ━━")
        # Cap per-kind detail at 8 to keep the digest readable; older
        # events get summarized with a "+N more" tail.
        for ev in kind_events[:8]:
            lines.append(f"• {ev.title}")
            if ev.message:
                # Indent + truncate to the first line of detail
                first = ev.message.split("\n", 1)[0]
                lines.append(f"  {first}")
        if len(kind_events) > 8:
            lines.append(f"  …and {len(kind_events) - 8} more")
        lines.append("")

    return title, "\n".join(lines).rstrip()


async def flush_digest(*, force: bool = False) -> int:
    """Drain the digest queue and send a consolidated notification.

    Returns the number of events flushed. No-op if the queue is empty
    (unless `force=True`, in which case still returns 0 silently).
    """
    s = load_settings()
    if not s.get("ntfy_digest_enabled") and not force:
        return 0

    events = await drain_digest()
    if not events:
        return 0

    schedule = s.get("ntfy_digest_schedule", "daily").capitalize()
    title, message = _format_digest(events, schedule)
    await send(title=title, message=message, tags=["mailbox_with_mail"])
    logger.info(f"Sent {schedule.lower()} digest with {len(events)} event(s)")
    return len(events)


def _seconds_until_next_run(schedule: str, now: Optional[datetime] = None) -> float:
    """Compute seconds until the next 09:00 fire for the given schedule."""
    now = now or datetime.now()
    target = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if schedule == "weekly":
        # Monday is weekday 0
        days_ahead = (0 - now.weekday()) % 7
        if days_ahead == 0 and now >= target:
            days_ahead = 7
        target = target + timedelta(days=days_ahead)
    else:
        if now >= target:
            target = target + timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


async def run_digest_scheduler() -> None:
    """Long-running coroutine that fires flush_digest() on the configured
    cadence. Designed to be spawned as a background task in main.py's
    lifespan and cancelled on shutdown.

    Re-reads settings each iteration so toggling digest mode or changing
    the schedule takes effect without a restart.
    """
    logger.info("Digest scheduler started")
    try:
        while True:
            s = load_settings()
            schedule = s.get("ntfy_digest_schedule", "daily")
            wait = _seconds_until_next_run(schedule)
            logger.debug(f"Digest scheduler sleeping {wait:.0f}s until next {schedule} fire")
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                # On shutdown, flush whatever we've got so the user
                # doesn't lose events queued during the day.
                logger.info("Digest scheduler cancelled — flushing remaining events")
                try:
                    await flush_digest(force=True)
                except Exception:
                    logger.warning("Final digest flush failed", exc_info=True)
                raise
            try:
                await flush_digest()
            except Exception:
                logger.warning("Digest flush failed", exc_info=True)
    except asyncio.CancelledError:
        raise
