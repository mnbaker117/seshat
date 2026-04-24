"""
Unit tests for `app.orchestrator.sse_broadcast` — the per-client queue
fanout that backs the SSE route.
"""
from __future__ import annotations

import asyncio
import pytest

from app.orchestrator import sse_broadcast


@pytest.fixture(autouse=True)
def _reset():
    sse_broadcast.reset_for_tests()
    yield
    sse_broadcast.reset_for_tests()


class TestPublish:
    async def test_publish_without_subscribers_is_noop(self):
        # Should not raise even when nobody is listening — publishers
        # call this from hot loops that shouldn't know about clients.
        await sse_broadcast.publish("torrent-progress", {"hash": "abc"})
        assert sse_broadcast.subscriber_count() == 0

    async def test_one_subscriber_receives_published_event(self):
        q = sse_broadcast.register()
        try:
            await sse_broadcast.publish("toast", {"level": "info", "message": "hi"})
            event_type, data = await asyncio.wait_for(q.get(), timeout=1)
            assert event_type == "toast"
            assert data == {"level": "info", "message": "hi"}
        finally:
            sse_broadcast.unregister(q)

    async def test_multiple_subscribers_each_receive_event(self):
        q1 = sse_broadcast.register()
        q2 = sse_broadcast.register()
        try:
            assert sse_broadcast.subscriber_count() == 2
            await sse_broadcast.publish("mam-stats", {"ratio": 42.0})
            e1 = await asyncio.wait_for(q1.get(), timeout=1)
            e2 = await asyncio.wait_for(q2.get(), timeout=1)
            assert e1 == ("mam-stats", {"ratio": 42.0})
            assert e2 == ("mam-stats", {"ratio": 42.0})
        finally:
            sse_broadcast.unregister(q1)
            sse_broadcast.unregister(q2)

    async def test_unregister_drops_subscriber_count(self):
        q = sse_broadcast.register()
        assert sse_broadcast.subscriber_count() == 1
        sse_broadcast.unregister(q)
        assert sse_broadcast.subscriber_count() == 0

    async def test_unregister_is_idempotent(self):
        q = sse_broadcast.register()
        sse_broadcast.unregister(q)
        # Second unregister is a no-op — must not raise.
        sse_broadcast.unregister(q)
        assert sse_broadcast.subscriber_count() == 0


class TestDropPolicy:
    async def test_full_queue_drops_events_without_raising(self, monkeypatch):
        # Shrink the cap so the test doesn't have to enqueue 64 items.
        monkeypatch.setattr(sse_broadcast, "_CLIENT_QUEUE_MAX", 2)
        q = sse_broadcast.register()
        try:
            await sse_broadcast.publish("torrent-progress", {"i": 1})
            await sse_broadcast.publish("torrent-progress", {"i": 2})
            # Third publish overflows — publisher must not raise.
            await sse_broadcast.publish("torrent-progress", {"i": 3})
            first = await asyncio.wait_for(q.get(), timeout=1)
            second = await asyncio.wait_for(q.get(), timeout=1)
            assert first[1]["i"] == 1
            assert second[1]["i"] == 2
            # Third was dropped.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(q.get(), timeout=0.1)
        finally:
            sse_broadcast.unregister(q)

    async def test_full_queue_only_affects_slow_client(self, monkeypatch):
        # Slow client's queue caps at 1 so it overflows immediately;
        # the fast client's queue is registered afterward under the
        # default cap so it doesn't.
        monkeypatch.setattr(sse_broadcast, "_CLIENT_QUEUE_MAX", 1)
        q_slow = sse_broadcast.register()
        monkeypatch.setattr(sse_broadcast, "_CLIENT_QUEUE_MAX", 64)
        q_fast = sse_broadcast.register()
        try:
            await sse_broadcast.publish("toast", {"i": 1})
            await sse_broadcast.publish("toast", {"i": 2})
            # Fast client gets both.
            e1 = await asyncio.wait_for(q_fast.get(), timeout=1)
            e2 = await asyncio.wait_for(q_fast.get(), timeout=1)
            assert e1[1]["i"] == 1
            assert e2[1]["i"] == 2
            # Slow client only got the first — second overflowed.
            s1 = await asyncio.wait_for(q_slow.get(), timeout=1)
            assert s1[1]["i"] == 1
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(q_slow.get(), timeout=0.1)
        finally:
            sse_broadcast.unregister(q_slow)
            sse_broadcast.unregister(q_fast)
