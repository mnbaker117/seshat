"""
HTTP-level tests for the settings router.

Isolates settings.json to a tmp_path file per test so we don't touch
the real data dir. Verifies:
  - GET redacts secret keys and adds _configured siblings
  - PATCH updates whitelisted fields and persists them
  - PATCH rejects keys outside the whitelist
  - Sparse PATCH (one key) doesn't clobber others
"""
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app import config
from app.routers.settings import router as settings_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(settings_router)
    return app


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Point config.SETTINGS_PATH + load_settings cache at tmp_path."""
    p = tmp_path / "settings.json"
    seed = {
        **config.DEFAULT_SETTINGS,
        "mam_session_id": "SECRET_TOKEN",
        "qbit_password": "SECRET_PW",
        "ntfy_url": "https://ntfy.sh",
        "review_queue_enabled": True,
        "snatch_budget_cap": 200,
        "daily_digest_hour": 9,
    }
    p.write_text(json.dumps(seed))
    monkeypatch.setattr(config, "SETTINGS_PATH", p)
    # Invalidate the mtime cache so the next load_settings() re-reads.
    config._settings_cache["data"] = None
    config._settings_cache["mtime"] = object()
    yield p
    config._settings_cache["data"] = None
    config._settings_cache["mtime"] = object()


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


class TestGetSettings:
    async def test_secrets_redacted(self, isolated_settings):
        async with await _client(_make_app()) as c:
            r = await c.get("/api/v1/settings")
            assert r.status_code == 200
            body = r.json()
            assert "mam_session_id" not in body
            assert body["mam_session_id_configured"] is True
            assert body["qbit_password_configured"] is True
            # ntfy_url is no longer a secret — it's a plain setting
            assert "ntfy_url" in body

    async def test_non_secrets_visible(self, isolated_settings):
        async with await _client(_make_app()) as c:
            body = (await c.get("/api/v1/settings")).json()
            assert body["review_queue_enabled"] is True
            assert body["snatch_budget_cap"] == 200
            assert body["daily_digest_hour"] == 9


class TestPatchSettings:
    async def test_updates_whitelisted_field(self, isolated_settings):
        async with await _client(_make_app()) as c:
            r = await c.patch(
                "/api/v1/settings",
                json={"daily_digest_hour": 14},
            )
            assert r.status_code == 200
            assert r.json()["updated"] == ["daily_digest_hour"]

            # Persisted to disk.
            saved = json.loads(Path(isolated_settings).read_text())
            assert saved["daily_digest_hour"] == 14

    async def test_rejects_non_whitelisted_key(self, isolated_settings):
        async with await _client(_make_app()) as c:
            r = await c.patch(
                "/api/v1/settings",
                json={"mam_session_id": "INJECTED"},
            )
            assert r.status_code == 200
            body = r.json()
            assert "mam_session_id" in body["rejected"]
            assert body["updated"] == []

            saved = json.loads(Path(isolated_settings).read_text())
            assert saved["mam_session_id"] == "SECRET_TOKEN"  # unchanged

    async def test_sparse_patch_preserves_other_keys(self, isolated_settings):
        async with await _client(_make_app()) as c:
            await c.patch(
                "/api/v1/settings",
                json={"snatch_budget_cap": 150},
            )
            saved = json.loads(Path(isolated_settings).read_text())
            assert saved["snatch_budget_cap"] == 150
            # Unchanged keys still present.
            assert saved["daily_digest_hour"] == 9
            assert saved["mam_session_id"] == "SECRET_TOKEN"

    async def test_noop_patch_returns_empty_updated(self, isolated_settings):
        async with await _client(_make_app()) as c:
            r = await c.patch(
                "/api/v1/settings",
                json={"snatch_budget_cap": 200},
            )
            assert r.json()["updated"] == []
