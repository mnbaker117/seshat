"""
Live integration smoke tests — skipped by default.

These hit a running Seshat container (and optionally an ABS
container) to catch regressions that the in-process unit tests
can't see: Docker image wiring, live DB schema on a prod-shape
install, reverse-proxy paths, and the real ABS API contract.

Enabled by environment variables:

    SESHAT_LIVE_URL=http://10.0.10.20:8789
    SESHAT_SESSION=<value of the seshat_session cookie>

Pull the cookie value from your browser devtools AFTER logging in to
the running container — it's signed and opaque, not a password, so
pasting it into a shell env var on a trusted host is safe. The
AuthMiddleware rejects everything under /api/* without it.

Optional ABS checks run when these are ALSO set:

    ABS_URL=http://10.0.10.20:13378
    ABS_API_KEY=<token>

No credentials are committed — the live container has its own
encrypted secret store, so these env vars only matter when the
developer wants to validate the ABS side directly.

Reason for deferring full docker-compose-driven CI:
* ABS needs a seeded audio library to be interesting — setting that
  up in CI is non-trivial.
* The user already smoke-tests each deploy against the live stack;
  these tests formalize that pass so it survives a context switch.
"""
from __future__ import annotations

import os

import httpx
import pytest

SESHAT_LIVE_URL = os.environ.get("SESHAT_LIVE_URL", "").rstrip("/")
SESHAT_SESSION = os.environ.get("SESHAT_SESSION", "")
ABS_URL = os.environ.get("ABS_URL", "").rstrip("/")
ABS_API_KEY = os.environ.get("ABS_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not SESHAT_LIVE_URL,
    reason="SESHAT_LIVE_URL not set — live integration tests opt-in only",
)


@pytest.fixture
async def live_client():
    """httpx client pointed at the live Seshat container.

    Attaches the `seshat_session` cookie if `SESHAT_SESSION` is set —
    AuthMiddleware rejects /api/* without it. Leaving the var unset
    lets the developer validate the 401-path directly.
    """
    cookies = {"seshat_session": SESHAT_SESSION} if SESHAT_SESSION else {}
    async with httpx.AsyncClient(
        base_url=SESHAT_LIVE_URL, timeout=15.0, cookies=cookies,
    ) as c:
        yield c


class TestSeshatHealth:
    async def test_health_endpoint_ok(self, live_client):
        r = await live_client.get("/api/discovery/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    async def test_version_endpoint_reports_sha(self, live_client):
        r = await live_client.get("/api/discovery/version")
        assert r.status_code == 200
        body = r.json()
        # Either a git SHA (hex) or the "dev" sentinel for local builds.
        assert body.get("sha")
        assert body.get("short_sha")

    async def test_platform_reports_first_run_flag(self, live_client):
        r = await live_client.get("/api/discovery/platform")
        assert r.status_code == 200
        body = r.json()
        assert "first_run" in body
        assert "default_library_paths" in body


class TestSeshatDiscovery:
    async def test_stats_returns_active_library(self, live_client):
        r = await live_client.get("/api/discovery/stats")
        assert r.status_code == 200
        body = r.json()
        # Keys shape — not specific values (those depend on user's library).
        for k in (
            "total_books", "owned_books", "authors",
            "library_slug", "library_name", "content_type",
        ):
            assert k in body, f"stats missing key: {k}"

    async def test_works_list_endpoint(self, live_client):
        r = await live_client.get("/api/v1/works")
        assert r.status_code == 200
        body = r.json()
        assert "total" in body
        assert "items" in body
        assert isinstance(body["items"], list)

    async def test_metadata_sources_endpoint(self, live_client):
        """The Phase 7 unified panel — regression guard for the
        migration from legacy `*_enabled` keys."""
        r = await live_client.get("/api/v1/metadata-sources")
        assert r.status_code == 200
        body = r.json()
        # Shape: {state: {sources, priority}, known: [...], derived: {...}}
        assert "state" in body
        assert "known" in body
        # MAM is the always-first pinned row — should always be present.
        known_names = {k["name"] for k in body["known"]}
        assert "mam" in known_names


@pytest.mark.skipif(
    not (ABS_URL and ABS_API_KEY),
    reason="ABS_URL and ABS_API_KEY required for ABS integration",
)
class TestAbsIntegration:
    async def test_abs_ping_returns_libraries(self):
        """Validates the ABS credentials AND that the API shape still
        matches what `AudiobookshelfApp.get_libraries` expects."""
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{ABS_URL}/api/libraries",
                headers={"Authorization": f"Bearer {ABS_API_KEY}"},
            )
            assert r.status_code == 200
            body = r.json()
            # ABS returns {"libraries": [...]} as of v2.
            assert isinstance(body, dict)
            assert "libraries" in body
