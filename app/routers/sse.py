"""
Server-Sent Events route — `GET /api/v1/events`.

One long-lived HTTP connection per browser tab. The frontend's
`useVisibleEventSource` hook opens this endpoint and consumes the
event stream; backend publishers (budget watcher, inject, economy,
scan) push events via `app.orchestrator.sse_broadcast.publish`.

Event format (one `ServerSentEvent` per `publish` call):
    event: <event_type>
    data:  <json-encoded payload>
    id:    <auto-incrementing monotonic sequence>

Event types currently emitted (commits 2-4 of the Tier 2 sequence):
  * torrent-progress — per-torrent progress delta from qBit snapshot
  * client-status    — qBit reachability transition
  * mam-stats        — ratio / seedbonus / upload_buffer_bytes refresh
  * toast            — ephemeral in-browser notification

Keepalive: sse-starlette sends `: ping` comments every `ping` seconds
to keep proxies from idling the connection. 15s matches the default
nginx `proxy_read_timeout` buffer without being chatty.
"""
from __future__ import annotations

import json
import logging
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.orchestrator import sse_broadcast

logger = logging.getLogger("seshat.sse")

router = APIRouter(prefix="/api/v1", tags=["sse"])


@router.get("/events")
async def events(request: Request) -> EventSourceResponse:
    """Subscribe to the server event stream.

    Returns an `EventSourceResponse` that stays open until the client
    disconnects. Each event published via `sse_broadcast.publish` is
    forwarded to this client's queue; the generator below yields one
    `ServerSentEvent` per queue item.

    Subscriber registration happens synchronously HERE (not inside
    the generator) so a publish that races with connection setup
    still reaches this client — the generator body doesn't start
    running until the response is committed and the client is reading,
    which can lag behind the backend's reaction to an API call.
    """
    queue = sse_broadcast.register()
    logger.debug(
        "SSE client connected (%d total)",
        sse_broadcast.subscriber_count(),
    )

    async def event_generator():
        try:
            seq = 0
            while True:
                if await request.is_disconnected():
                    break
                event_type, data = await queue.get()
                seq += 1
                yield ServerSentEvent(
                    data=json.dumps(data, default=str),
                    event=event_type,
                    id=str(seq),
                )
        finally:
            sse_broadcast.unregister(queue)
            logger.debug(
                "SSE client disconnected (%d remaining)",
                sse_broadcast.subscriber_count(),
            )

    return EventSourceResponse(event_generator(), ping=15)
