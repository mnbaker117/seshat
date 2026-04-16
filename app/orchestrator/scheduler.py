"""
APScheduler wiring for digest jobs.

Sets up a process-wide AsyncIOScheduler with three cron-style jobs:

  - `daily_digest` — fires at `daily_digest_hour` local time every day
  - `weekly_digest` — fires Sundays at 23:30 local time
  - `weekly_calibre_audit` — fires Sundays at 22:30 local time (one
    hour before the weekly digest so any discrepancies surface in the
    same window the user reviews). Skipped when ctx.calibre_library_path
    is empty — the job coroutine no-ops early.

The scheduler is owned by `main.py`'s lifespan — it's started after
the dispatcher is built and stopped cleanly during shutdown. All
jobs wrap their coroutines in broad exception handling so a crash
in one job doesn't kill the scheduler thread.

Why APScheduler vs a hand-rolled asyncio loop: digest cadence is
measured in hours and days, and cron-style "fire at 9am every day"
is awkward to express without APScheduler's trigger classes. The
cookie keep-alive and review-timeout jobs use simple interval loops
because their cadence is pure interval; these ones want calendar
semantics.
"""
from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.notify.digests import (
    DigestContext, run_daily, run_weekly, run_calibre_audit,
)

_log = logging.getLogger("seshat.orchestrator.scheduler")


def build_scheduler(
    *,
    daily_digest_hour: int,
    ctx: DigestContext,
    timezone: str = "local",
) -> AsyncIOScheduler:
    """Construct a scheduler with the digest jobs wired in.

    The caller is responsible for calling .start() and .shutdown().
    """
    scheduler = AsyncIOScheduler(timezone=None if timezone == "local" else timezone)

    async def _daily_job():
        _log.info("daily digest tick")
        try:
            await run_daily(ctx)
        except Exception:
            _log.exception("daily digest crashed")

    async def _weekly_job():
        _log.info("weekly digest tick")
        try:
            await run_weekly(ctx)
        except Exception:
            _log.exception("weekly digest crashed")

    async def _calibre_audit_job():
        _log.info("weekly Calibre audit tick")
        try:
            await run_calibre_audit(ctx)
        except Exception:
            _log.exception("weekly Calibre audit crashed")

    scheduler.add_job(
        _daily_job,
        trigger=CronTrigger(hour=int(daily_digest_hour), minute=0),
        id="daily_digest",
        name="Daily digest",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _weekly_job,
        trigger=CronTrigger(day_of_week="sun", hour=23, minute=30),
        id="weekly_digest",
        name="Weekly digest",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    # Fire the audit an hour before the weekly digest so any
    # discrepancies show up in the same review window. Job itself
    # no-ops when ctx.calibre_library_path is empty.
    scheduler.add_job(
        _calibre_audit_job,
        trigger=CronTrigger(day_of_week="sun", hour=22, minute=30),
        id="weekly_calibre_audit",
        name="Weekly Calibre audit",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    if not ctx.calibre_library_path:
        _log.info(
            "weekly_calibre_audit: disabled (no calibre_library_path configured); "
            "job will be a no-op"
        )

    return scheduler
