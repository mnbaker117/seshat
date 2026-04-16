"""
In-memory fake IRC server for unit tests.

We deliberately do NOT spin up a real TCP server (port allocation,
race conditions, slower test runs). Instead, the fake provides a
`(StreamReader, FakeStreamWriter)` pair that the IrcClient can use
exactly as if it had connected to a real socket. The reader is a
real `asyncio.StreamReader` whose buffer the test feeds bytes into;
the writer is a tiny class that captures everything the client
writes for assertions.

The fake exposes:

  - `connect_fn` — pass to `IrcClient(connect_fn=...)` so the client
    uses the fake instead of opening a real socket
  - `feed_line(line)` — push one CRLF-terminated line into the
    client's read buffer (simulating server → client traffic)
  - `feed_lines([...])` — convenience for batches
  - `written_lines()` — every CRLF-terminated line the client has
    written to us so far, decoded
  - `wait_for_line(pattern, timeout)` — async helper that waits until
    the client has written a line matching `pattern` (substring or
    regex). Used in tests to script the handshake step by step.
  - `eof()` — close the read side, simulating a server disconnect.

Test pattern (typical handshake test):

    async def test_sasl(fake_irc):
        client = IrcClient(config, on_announce, connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())

        # Drive the handshake — client sends, fake responds.
        await fake_irc.wait_for_line("CAP LS 302")
        fake_irc.feed_line(":server CAP * LS :sasl")

        await fake_irc.wait_for_line("CAP REQ :sasl")
        fake_irc.feed_lines([
            ":server CAP * ACK :sasl",
            ":server NOTICE * :auth in progress",
        ])
        ...
        await client.stop()
        await task
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional, Pattern, Union


class _FakeStreamWriter:
    """Captures everything the IRC client writes for test assertions.

    Duck-types as enough of `asyncio.StreamWriter` for IrcClient's
    needs: `write`, `drain` (no-op), `close`, `wait_closed`. The
    captured bytes are exposed via the parent FakeIrc instance.
    """

    def __init__(self, fake: "FakeIrc") -> None:
        self._fake = fake
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        self._fake._on_write(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self._closed = True
        self._fake._on_writer_close()

    async def wait_closed(self) -> None:
        return None


class FakeIrc:
    """Programmable in-memory fake for the IRC server.

    See module docstring for usage. The fake is single-use — calling
    `connect_fn` returns a fresh reader/writer pair every time, so a
    test can exercise the reconnect path by reusing the same FakeIrc
    instance across multiple connection cycles. The `_write_buffer`
    and `_lines_event` are reset on each connect so assertions don't
    bleed across cycles.
    """

    def __init__(self) -> None:
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[_FakeStreamWriter] = None
        self._write_buffer: bytearray = bytearray()
        self._lines_event = asyncio.Event()
        self.writer_closed = False
        self.connect_count = 0

    # ─── Connection factory injected into IrcClient ──────────

    async def connect_fn(self) -> tuple[asyncio.StreamReader, _FakeStreamWriter]:
        self.connect_count += 1
        # Fresh reader/writer pair and a fresh write buffer for each
        # connection cycle so reconnect tests can assert against just
        # the most recent cycle's writes.
        #
        # CRITICAL: do NOT replace `_lines_event` here. The test
        # might already have a reference to the existing event from
        # a wait_for_line() that started before the client task ran
        # connect_fn — replacing the event would orphan that wait
        # and the test would hang for the full timeout. Just clear()
        # the existing event so future writes generate fresh
        # notifications.
        self._reader = asyncio.StreamReader()
        self._writer = _FakeStreamWriter(self)
        self._write_buffer = bytearray()
        self._lines_event.clear()
        self.writer_closed = False
        return self._reader, self._writer

    # ─── Server → client (feeding the read buffer) ───────────

    def feed_line(self, line: str) -> None:
        """Push one line into the client's read buffer.

        The trailing `\\r\\n` is added automatically — pass the line
        without it. The line is encoded as UTF-8.
        """
        if self._reader is None:
            raise RuntimeError("feed_line called before client connected")
        self._reader.feed_data((line + "\r\n").encode("utf-8"))

    def feed_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.feed_line(line)

    def eof(self) -> None:
        """Signal end-of-stream on the read side (server disconnect)."""
        if self._reader is not None:
            self._reader.feed_eof()

    # ─── Client → server (capturing writes) ──────────────────

    def _on_write(self, data: bytes) -> None:
        self._write_buffer.extend(data)
        # Wake any task currently awaiting a new line.
        self._lines_event.set()

    def _on_writer_close(self) -> None:
        self.writer_closed = True
        self._lines_event.set()
        # Mirror real TCP socket behavior: closing the writer side
        # causes the reader to see EOF. Without this, an IrcClient
        # parked in readline() never wakes up after stop(), and the
        # test's wait_for(task) deadlocks.
        if self._reader is not None and not self._reader.at_eof():
            self._reader.feed_eof()

    def written_lines(self) -> list[str]:
        """Decode the captured write buffer into a list of CRLF-split lines."""
        text = bytes(self._write_buffer).decode("utf-8", errors="replace")
        return [line for line in text.split("\r\n") if line]

    async def wait_for_line(
        self,
        pattern: Union[str, Pattern[str]],
        timeout: float = 2.0,
    ) -> str:
        """Wait until the client writes a line matching `pattern`.

        `pattern` can be a substring (str) or a compiled regex. The
        match is searched in every written line. Returns the first
        matching line. Raises asyncio.TimeoutError on timeout.

        Used to script the handshake: the test waits for each
        expected client message before pushing the corresponding
        server response into the read buffer.
        """
        deadline = asyncio.get_event_loop().time() + timeout

        def _matches(line: str) -> bool:
            if isinstance(pattern, str):
                return pattern in line
            return bool(pattern.search(line))

        while True:
            for line in self.written_lines():
                if _matches(line):
                    return line
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"timeout waiting for IRC client to write line matching "
                    f"{pattern!r}; written so far: {self.written_lines()}"
                )
            self._lines_event.clear()
            try:
                await asyncio.wait_for(
                    self._lines_event.wait(), timeout=remaining
                )
            except asyncio.TimeoutError:
                continue

    def clear_writes(self) -> None:
        """Discard everything the client has written so far.

        Used between handshake stages to keep `written_lines()`
        focused on what the test cares about right now.
        """
        self._write_buffer = bytearray()


# ─── Helper: drive a complete SASL handshake ─────────────────


async def drive_sasl_handshake(
    fake: FakeIrc,
    *,
    nick: str = "testbot",
) -> None:
    """Walk a FakeIrc through a successful SASL handshake.

    Removes ~30 lines of boilerplate from every test that just wants
    a connected, joined IrcClient as its starting state.

    Calls `clear_writes()` between steps so each `wait_for_line` only
    sees genuinely new client output. Without that, the regex used to
    match the base64 AUTHENTICATE payload (`^AUTHENTICATE [A-Za-z0-9+/=]+`)
    matches the literal `AUTHENTICATE PLAIN` line that came earlier
    and returns immediately, breaking the handshake sequencing.
    """
    await fake.wait_for_line("CAP LS 302")
    fake.feed_line(":server CAP * LS :sasl")

    await fake.wait_for_line("CAP REQ :sasl")
    fake.feed_line(":server CAP * ACK :sasl")

    await fake.wait_for_line("AUTHENTICATE PLAIN")
    fake.clear_writes()
    fake.feed_line("AUTHENTICATE +")

    # The base64 payload itself; we don't care about the value here.
    await fake.wait_for_line(re.compile(r"^AUTHENTICATE [A-Za-z0-9+/=]+$"))
    fake.feed_line(f":server 903 {nick} :SASL authentication successful")

    await fake.wait_for_line("CAP END")
    fake.feed_line(f":server 001 {nick} :Welcome to the test IRC server")

    await fake.wait_for_line("JOIN #announce")
    fake.feed_line(f":{nick}!user@host JOIN :#announce")
