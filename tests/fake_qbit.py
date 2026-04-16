"""
Fake qBittorrent WebUI server for unit tests.

Same shape as `tests/fake_mam.py` — programmable response builder
backed by `httpx.MockTransport`. Tests construct a `FakeQbit`,
optionally override response fields, and pass `fake.transport()`
into a real `QbitClient` instance via the client's `transport=`
constructor parameter. No real network is touched.

The fake simulates four pieces of qBit's `/api/v2/` surface:

  - POST /api/v2/auth/login    — credential check, SID cookie issue
  - POST /api/v2/torrents/add  — multipart upload, success/fail toggle
  - GET  /api/v2/torrents/info — JSON list, optional category filter
  - (anything else)            — 404

Session state is real: a successful login issues an SID cookie, and
subsequent requests must present that cookie or get 403. Tests can
override `valid_credentials`, `add_status`, etc. to drive specific
failure scenarios.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class FakeQbit:
    """Programmable fake for the qBittorrent WebUI API.

    Defaults model the happy path:
      - login succeeds with `admin / adminadmin`
      - add returns 200 "Ok."
      - list returns whatever's been added through this fake instance

    Tests override fields for failure scenarios:
        fake.valid_credentials = ("user", "wrongpass")  # forces login fail
        fake.add_status = 415                            # forces invalid file
        fake.require_auth = False                        # disables session check

    Captured state:
      - `requests` — every intercepted httpx.Request, in order
      - `added_torrents` — list of dicts describing successful adds
        (category, save_path, file_size). Tests can assert on these
        to verify the right form data was sent.
      - `torrents` — what list_torrents returns. Pre-populated to empty;
        successful adds append a synthetic entry so the round-trip
        list-after-add path can be exercised.
    """

    valid_credentials: tuple[str, str] = ("admin", "adminadmin")
    sid_cookie: str = "fakeSID12345"
    require_auth: bool = True

    # Per-endpoint response overrides. Status alone is enough for most
    # failure scenarios; body overrides exist for the cases where the
    # client cares about the body content (login "Ok." vs "Fails.").
    login_status: int = 200
    login_body_override: Optional[bytes] = None  # None = compute from creds
    add_status: int = 200
    add_body: bytes = b"Ok."
    info_status: int = 200

    # Captured state for assertions.
    requests: list[httpx.Request] = field(default_factory=list)
    added_torrents: list[dict] = field(default_factory=list)
    torrents: list[dict] = field(default_factory=list)

    def _is_authed(self, request: httpx.Request) -> bool:
        if not self.require_auth:
            return True
        cookie = request.headers.get("cookie", "")
        return f"SID={self.sid_cookie}" in cookie

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if path == "/api/v2/auth/login":
            return self._handle_login(request)
        if path == "/api/v2/torrents/add":
            return self._handle_add(request)
        if path == "/api/v2/torrents/info":
            return self._handle_info(request)

        return httpx.Response(
            404,
            content=b"unknown fake-qbit endpoint",
            headers={"content-type": "text/plain"},
        )

    # ─── Endpoint handlers ───────────────────────────────────

    def _handle_login(self, request: httpx.Request) -> httpx.Response:
        if self.login_status != 200:
            return httpx.Response(self.login_status, content=b"")

        # Body override (test forcing a specific response) bypasses
        # credential checking entirely.
        if self.login_body_override is not None:
            return httpx.Response(200, content=self.login_body_override)

        body = request.content.decode("utf-8", errors="replace")
        # Form-encoded: username=foo&password=bar
        params = dict(p.split("=", 1) for p in body.split("&") if "=" in p)
        user = params.get("username", "")
        pwd = params.get("password", "")

        if (user, pwd) == self.valid_credentials:
            return httpx.Response(
                200,
                content=b"Ok.",
                headers={
                    "set-cookie": f"SID={self.sid_cookie}; HttpOnly; path=/",
                    "content-type": "text/plain",
                },
            )

        return httpx.Response(200, content=b"Fails.", headers={"content-type": "text/plain"})

    def _handle_add(self, request: httpx.Request) -> httpx.Response:
        if not self._is_authed(request):
            return httpx.Response(403, content=b"forbidden")

        # Allow tests to force a specific failure status.
        if self.add_status != 200:
            return httpx.Response(self.add_status, content=self.add_body)

        # Parse the multipart body well enough to capture form fields
        # for test assertions. Full multipart parsing is overkill —
        # we just need to find the named fields.
        body = request.content
        captured = {
            "category": _extract_form_field(body, "category"),
            "save_path": _extract_form_field(body, "savepath"),
            "tags": _extract_form_field(body, "tags"),
            "torrent_size": _extract_torrent_size(body),
        }
        self.added_torrents.append(captured)

        # Append a synthetic entry to the listing so a list-after-add
        # roundtrip works in the same test.
        self.torrents.append(
            {
                "hash": f"hash{len(self.added_torrents):040d}"[:40],
                "name": "added.torrent",
                "category": captured["category"] or "",
                "state": "downloading",
                "seeding_time": 0,
                "save_path": captured["save_path"] or "",
                "added_on": 1234567890,
            }
        )

        return httpx.Response(200, content=self.add_body, headers={"content-type": "text/plain"})

    def _handle_info(self, request: httpx.Request) -> httpx.Response:
        if not self._is_authed(request):
            return httpx.Response(403, content=b"forbidden")

        if self.info_status != 200:
            return httpx.Response(self.info_status, content=b"")

        category_filter = request.url.params.get("category")
        result = self.torrents
        if category_filter:
            result = [t for t in result if t.get("category") == category_filter]

        return httpx.Response(
            200,
            content=json.dumps(result).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    # ─── Public surface ──────────────────────────────────────

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)


def _extract_form_field(body: bytes, name: str) -> Optional[str]:
    """Pull a named form field value out of a multipart body.

    Crude — looks for `name="<field>"\\r\\n\\r\\n<value>\\r\\n` and
    returns the value. Good enough for test assertions; a real
    multipart parser would be overkill here.
    """
    needle = f'name="{name}"'.encode("utf-8")
    idx = body.find(needle)
    if idx < 0:
        return None
    # Skip past the header lines: name="..." [content-type] \r\n\r\n
    sep_idx = body.find(b"\r\n\r\n", idx)
    if sep_idx < 0:
        return None
    value_start = sep_idx + 4
    value_end = body.find(b"\r\n", value_start)
    if value_end < 0:
        return None
    return body[value_start:value_end].decode("utf-8", errors="replace")


def _extract_torrent_size(body: bytes) -> int:
    """Find the multipart `torrents` field and return its byte length."""
    needle = b'name="torrents"'
    idx = body.find(needle)
    if idx < 0:
        return 0
    sep_idx = body.find(b"\r\n\r\n", idx)
    if sep_idx < 0:
        return 0
    value_start = sep_idx + 4
    # The closing boundary starts with \r\n--<boundary>; find it.
    value_end = body.find(b"\r\n--", value_start)
    if value_end < 0:
        return 0
    return value_end - value_start
