"""
SSE event broadcast — per-client asyncio.Queue fanout.

Backend publishers call `publish(event_type, data)` to push an event to
every connected SSE client. Each subscriber gets its own bounded queue;
slow/disconnected clients get dropped rather than back-pressuring
publishers. The queue is bounded because the publish path runs inside
hot loops (budget watcher, download watcher) — we'd rather lose a
progress tick than stall the polling thread.

Usage from a publisher:
    from app.orchestrator import sse_broadcast
    await sse_broadcast.publish("torrent-progress", {"hash": "...", "progress": 0.5})

Usage from a subscriber (SSE route):
    queue = sse_broadcast.register()
    try:
        while True:
            event = await queue.get()
            yield event
    finally:
        sse_broadcast.unregister(queue)

`register()` is sync on purpose so the route handler can register
BEFORE it returns the `EventSourceResponse` generator — otherwise the
subscriber doesn't exist until the generator's first `await queue.get()`
runs, which is too late for any publish that happens immediately
after the HTTP connection is established.

Events are `(event_type, data)` tuples — the route converts them to
`EventSourceResponse` ServerSentEvent objects.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("seshat.sse")

# Cap per-client buffer so a slow HTTP consumer can't grow unbounded.
# At ~1 tick/sec (budget watcher cadence) + other event types, a 64-slot
# buffer tolerates several seconds of client slowness before drops.
_CLIENT_QUEUE_MAX = 64

# Module-global registry of active subscriber queues. Protected by
# the GIL for set add/discard (these are single-bytecode ops) — no
# explicit lock needed on CPython. Access is fan-out only, not
# mutable iteration, so concurrent publish + (un)subscribe is safe.
_subscribers: set[asyncio.Queue[tuple[str, Any]]] = set()


def subscriber_count() -> int:
    """Return the number of currently-connected SSE clients.

    Useful for publishers to skip expensive diff work when no one is
    listening — e.g. budget watcher can elide progress events entirely
    if the UI isn't subscribed.
    """
    return len(_subscribers)


async def publish(event_type: str, data: Any) -> None:
    """Enqueue an event to every connected subscriber.

    On a full queue we drop the event for that client only (log once at
    debug level) rather than stalling the publisher. Publishers run
    inside hot loops — a dropped UI tick is cheap, a stalled backend
    poll is expensive.
    """
    if not _subscribers:
        return
    event = (event_type, data)
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            logger.debug("SSE client queue full — dropping %s event", event_type)


def register() -> asyncio.Queue[tuple[str, Any]]:
    """Create + register a new subscriber queue.

    Returns the queue immediately — no await, no context manager — so
    the SSE route handler can register synchronously before returning
    the response. This closes the race where a publish fired between
    route-handler-return and first-generator-yield would be silently
    dropped because the subscriber wasn't tracked yet.

    Callers MUST eventually call `unregister(queue)`, typically in a
    `finally` block inside the event generator.
    """
    q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAX)
    _subscribers.add(q)
    return q


def unregister(queue: asyncio.Queue[tuple[str, Any]]) -> None:
    """Remove a subscriber queue. Idempotent — safe to call twice."""
    _subscribers.discard(queue)


def reset_for_tests() -> None:
    """Clear the subscriber set — for test teardown only."""
    _subscribers.clear()
