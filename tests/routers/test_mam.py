"""
HTTP-level tests for the MAM status / cookie router.

Stubs out the network-touching helpers (`get_user_status`,
`mam_cookie.validate`) and isolates settings.json to a tmp_path
file so we never touch the real data dir or real MAM. Verifies:

  - GET /status returns cookie_configured=False when settings empty
  - GET /status surfaces UserStatusError as `error` instead of 500
  - GET /status returns the parsed UserStatus on the happy path
  - POST /validate persists the validation result to settings
  - POST /cookie rejects empty / too-short payloads
  - POST /cookie persists + validates a fresh paste
"""
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app import config
from app.mam.user_status import UserStatus, UserStatusError
from app.routers import mam as mam_router_module


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(mam_router_module.router)
    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    seed = {**config.DEFAULT_SETTINGS, "mam_session_id": ""}
    p.write_text(json.dumps(seed))
    monkeypatch.setattr(config, "SETTINGS_PATH", p)
    config._settings_cache["data"] = None
    config._settings_cache["mtime"] = object()
    yield p
    config._settings_cache["data"] = None
    config._settings_cache["mtime"] = object()


@pytest.fixture
def app(isolated_settings):
    return _make_app()


def _patch_user_status(monkeypatch, *, raise_with=None, status=None):
    async def fake(token=None, ttl=None):
        if raise_with is not None:
            raise raise_with
        return status

    monkeypatch.setattr(mam_router_module, "get_user_status", fake)


def _patch_validate(monkeypatch, *, success: bool, message: str = ""):
    async def fake(token, skip_ip_update=True):
        return {"success": success, "message": message,
                "ip_result": None, "search_result": None}

    monkeypatch.setattr(mam_router_module.mam_cookie, "validate", fake)


def _patch_set_token(monkeypatch):
    """Replace set_current_token with a recorder so the import-time
    cookie module isn't actually mutated by tests."""
    captured = {}

    def fake(token):
        captured["token"] = token

    monkeypatch.setattr(
        mam_router_module.mam_cookie, "set_current_token", fake
    )
    return captured


class TestStatus:
    async def test_no_cookie_configured(self, app):
        async with await _client(app) as c:
            r = await c.get("/api/v1/mam/status")
            body = r.json()
            assert body["cookie_configured"] is False
            assert body["error"]

    async def test_user_status_error_surfaces(
        self, app, isolated_settings, monkeypatch
    ):
        s = json.loads(Path(isolated_settings).read_text())
        s["mam_session_id"] = "abc-token-with-enough-bytes-to-look-real"
        Path(isolated_settings).write_text(json.dumps(s))
        config._settings_cache["data"] = None
        _patch_user_status(
            monkeypatch, raise_with=UserStatusError("HTTP 503 from MAM")
        )

        async with await _client(app) as c:
            r = await c.get("/api/v1/mam/status")
            body = r.json()
            assert body["cookie_configured"] is True
            assert body["error"] == "HTTP 503 from MAM"
            # Should not blow up with a 500.
            assert r.status_code == 200

    async def test_happy_path(self, app, isolated_settings, monkeypatch):
        s = json.loads(Path(isolated_settings).read_text())
        s["mam_session_id"] = "abc-token-with-enough-bytes-to-look-real"
        Path(isolated_settings).write_text(json.dumps(s))
        config._settings_cache["data"] = None
        _patch_user_status(
            monkeypatch,
            status=UserStatus(
                ratio=2.5,
                wedges=12,
                seedbonus=4500,
                classname="Power User",
                username="op",
                uid=1234,
                uploaded_bytes=1_000_000_000,
                downloaded_bytes=400_000_000,
            ),
        )

        async with await _client(app) as c:
            r = await c.get("/api/v1/mam/status")
            body = r.json()
            assert body["ratio"] == 2.5
            assert body["wedges"] == 12
            assert body["username"] == "op"
            assert body["validation_ok"] is True


class TestValidate:
    async def test_persists_success(
        self, app, isolated_settings, monkeypatch
    ):
        s = json.loads(Path(isolated_settings).read_text())
        s["mam_session_id"] = "abc-token-with-enough-bytes-to-look-real"
        Path(isolated_settings).write_text(json.dumps(s))
        config._settings_cache["data"] = None
        _patch_validate(monkeypatch, success=True, message="ok")

        async with await _client(app) as c:
            r = await c.post("/api/v1/mam/validate")
            assert r.json()["ok"] is True

        saved = json.loads(Path(isolated_settings).read_text())
        assert saved["mam_validation_ok"] is True
        assert saved["mam_last_validated_at"]

    async def test_persists_failure(
        self, app, isolated_settings, monkeypatch
    ):
        s = json.loads(Path(isolated_settings).read_text())
        s["mam_session_id"] = "abc-token-with-enough-bytes-to-look-real"
        Path(isolated_settings).write_text(json.dumps(s))
        config._settings_cache["data"] = None
        _patch_validate(
            monkeypatch, success=False, message="cookie expired"
        )

        async with await _client(app) as c:
            r = await c.post("/api/v1/mam/validate")
            assert r.json()["ok"] is False

        saved = json.loads(Path(isolated_settings).read_text())
        assert saved["mam_validation_ok"] is False


class TestEmergencyCookie:
    async def test_rejects_empty(self, app):
        async with await _client(app) as c:
            r = await c.post("/api/v1/mam/cookie", json={"cookie": ""})
            assert r.status_code == 400

    async def test_rejects_short(self, app):
        async with await _client(app) as c:
            r = await c.post("/api/v1/mam/cookie", json={"cookie": "tiny"})
            assert r.status_code == 400

    async def test_persists_and_validates(
        self, app, isolated_settings, monkeypatch
    ):
        captured = _patch_set_token(monkeypatch)
        _patch_validate(monkeypatch, success=True, message="welcome back")
        fresh = "x" * 200  # plausible length

        async with await _client(app) as c:
            r = await c.post("/api/v1/mam/cookie", json={"cookie": fresh})
            assert r.json()["ok"] is True

        # In-memory token updated.
        assert captured["token"] == fresh

        # Settings persisted.
        saved = json.loads(Path(isolated_settings).read_text())
        assert saved["mam_session_id"] == fresh
        assert saved["mam_validation_ok"] is True
