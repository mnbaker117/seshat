"""
In-memory ring buffer for the log viewer.

Captures the last N log records from the `athenascout` logger tree so
the log viewer page can serve them instantly without reading Docker logs.
Attached to the root `athenascout` logger at startup (main.py).
"""
import logging
from collections import deque
from typing import Optional


class RingBufferHandler(logging.Handler):
    """Handler that stores the last `capacity` formatted log lines."""

    def __init__(self, capacity: int = 1000):
        super().__init__()
        self._buffer: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord):
        try:
            self._buffer.append(self.format(record))
        except Exception:
            pass

    def get_lines(self, last_n: Optional[int] = None) -> list[str]:
        """Return the most recent log lines (all if last_n is None)."""
        if last_n is None:
            return list(self._buffer)
        return list(self._buffer)[-last_n:]

    def clear(self):
        self._buffer.clear()


# Module-level singleton — attached once at startup
_handler: Optional[RingBufferHandler] = None


def init_log_buffer(capacity: int = 1000) -> RingBufferHandler:
    """Create and attach the ring buffer handler to the athenascout logger."""
    global _handler
    _handler = RingBufferHandler(capacity=capacity)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    logging.getLogger("seshat.discovery").addHandler(_handler)
    return _handler


def get_log_lines(last_n: Optional[int] = None) -> list[str]:
    """Return recent log lines from the buffer."""
    if _handler is None:
        return []
    return _handler.get_lines(last_n)
