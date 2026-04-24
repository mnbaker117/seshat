"""
Unit test for the SSE route handler `/api/v1/events`.

Calls the handler directly rather than opening a streaming HTTP
connection — an ASGI-transport stream test fights sse-starlette's
disconnect detection and makes the test flaky. The handler's
contract is narrow: synchronously register a subscriber, return an
`EventSourceResponse`, unregister in the generator's `finally`.
That's what we assert here. End-to-end flow is covered by the
`TIER2_TEST_PLAN.md` smoke against a live server.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sse_starlette.sse import EventSourceResponse

from app.orchestrator import sse_broadcast
from app.routers.sse import events


@pytest.fixture(autouse=True)
def _reset():
    sse_broadcast.reset_for_tests()
    yield
    sse_broadcast.reset_for_tests()


async def test_handler_registers_subscriber_synchronously():
    """A publish issued before anyone reads the stream must still
    reach this client — the handler registers synchronously before
    returning, so subscriber_count jumps to 1 on the await-less path."""
    request = AsyncMock()
    request.is_disconnected = AsyncMock(return_value=False)

    assert sse_broadcast.subscriber_count() == 0
    resp = await events(request)
    assert isinstance(resp, EventSourceResponse)
    assert sse_broadcast.subscriber_count() == 1


async def test_generator_yields_published_events():
    """Drive the response's event_generator directly and confirm that
    a publish lands as a ServerSentEvent with the matching event type
    and JSON-serialized data."""
    request = AsyncMock()
    # Keep the disconnect probe returning False so the loop doesn't
    # short-circuit; we'll break out via a direct queue pop timeout.
    request.is_disconnected = AsyncMock(return_value=False)

    resp = await events(request)
    body_iter = resp.body_iterator

    await sse_broadcast.publish(
        "torrent-progress", {"hash": "abc", "progress": 0.5}
    )

    sse_event = await asyncio.wait_for(body_iter.__anext__(), timeout=2)
    # sse-starlette's ServerSentEvent stringifies itself in __str__,
    # but the raw object exposes .data + .event directly.
    assert sse_event.event == "torrent-progress"
    assert json.loads(sse_event.data) == {"hash": "abc", "progress": 0.5}


async def test_generator_unregisters_on_cancel():
    """Cancelling the consumer must run the generator's finally block
    and unregister the subscriber — otherwise a dropped HTTP client
    leaks a queue indefinitely."""
    request = AsyncMock()
    request.is_disconnected = AsyncMock(return_value=False)

    resp = await events(request)
    body_iter = resp.body_iterator

    # Spawn a task that pulls from the generator, then cancel it.
    async def _consume():
        await body_iter.__anext__()

    task = asyncio.create_task(_consume())
    # Let the task reach the await on queue.get().
    await asyncio.sleep(0.05)
    assert sse_broadcast.subscriber_count() == 1

    task.cancel()
    # Await task so the generator's finally has a chance to run.
    with pytest.raises(asyncio.CancelledError):
        await task
    # Async generator needs an explicit aclose to finalize after
    # the pulling task was cancelled — this is what sse-starlette
    # does internally when the HTTP connection drops.
    await body_iter.aclose()
    assert sse_broadcast.subscriber_count() == 0
