"""
Snatch budget tracking and queueing.

MAM caps the number of "active snatches" a user can have — a torrent
counts against the budget from the moment it lands in the download client
until it has accumulated 72 hours of seedtime (or until the user
removes it from qBit). Seshat must never exceed this cap, or the
user gets flagged and eventually banned.

Two collaborating components:

  - **`ledger`** — every grab Seshat submits to qBit gets a row
    in `snatch_ledger`. Background `budget_release` jobs poll qBit,
    update each row's `seeding_seconds`, and mark rows `released`
    once they hit the threshold (or vanish from qBit). Rows where
    `released_at IS NULL` count toward the budget.

  - **`queue`** — when an announce arrives but the budget is full,
    the .torrent file is fetched and the grab is parked in
    `pending_queue` instead of being submitted to qBit. A budget
    watcher pops the highest-priority pending grab and submits it
    whenever a ledger row releases.

The `decide_grab_action` function below is the pure decision point
that the IRC dispatcher and inject endpoint both consult. Splitting
the decision out from the persistence makes it trivially unit-testable
without any database fixtures.
"""
from dataclasses import dataclass
from typing import Literal


GrabAction = Literal["submit", "queue", "drop"]


@dataclass(frozen=True)
class GrabDecision:
    """Outcome of `decide_grab_action`.

    `action` is one of:
      - "submit"  → budget has room, fetch and submit immediately
      - "queue"   → budget is full, fetch and park in pending_queue
      - "drop"    → budget is full AND queue is full (or drop mode);
                    don't even fetch the .torrent file
    """

    action: GrabAction
    reason: str
    budget_used: int
    budget_cap: int
    queue_size: int
    queue_max: int


def decide_grab_action(
    *,
    budget_used: int,
    budget_cap: int,
    queue_size: int,
    queue_max: int,
    queue_mode_enabled: bool,
) -> GrabDecision:
    """Pure decision function — no DB writes, no I/O.

    The IRC dispatcher and the manual-grab inject endpoint both call
    this with current ledger and queue counts; the result tells them
    whether to fetch the .torrent file at all and where to put it.

    Decision matrix:

        budget has room                           → submit
        budget full + queue mode + queue has room → queue
        budget full + queue mode + queue full     → drop  (queue overflow)
        budget full + drop mode                   → drop  (configured)
    """
    if budget_used < budget_cap:
        return GrabDecision(
            action="submit",
            reason="budget_available",
            budget_used=budget_used,
            budget_cap=budget_cap,
            queue_size=queue_size,
            queue_max=queue_max,
        )

    if not queue_mode_enabled:
        return GrabDecision(
            action="drop",
            reason="budget_full_drop_mode",
            budget_used=budget_used,
            budget_cap=budget_cap,
            queue_size=queue_size,
            queue_max=queue_max,
        )

    if queue_size < queue_max:
        return GrabDecision(
            action="queue",
            reason="budget_full_queueing",
            budget_used=budget_used,
            budget_cap=budget_cap,
            queue_size=queue_size,
            queue_max=queue_max,
        )

    return GrabDecision(
        action="drop",
        reason="budget_full_queue_full",
        budget_used=budget_used,
        budget_cap=budget_cap,
        queue_size=queue_size,
        queue_max=queue_max,
    )
