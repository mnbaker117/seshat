"""
Tests for the metadata-sources router
(v2.11.0 Stage 5++ commit 6/6).

Targets the Stage 5++ additions: Amazon's `format` + `language`
config strings round-trip cleanly through GET + PUT
`/api/v1/metadata-sources`. Other sources' rows never carry these
fields (they're Amazon-private).
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from app import config
from app.routers.metadata_sources import router as metadata_sources_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(metadata_sources_router)
    return app


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Point config.SETTINGS_PATH at a tmp_path settings.json,
    pre-migrated via source_config.migrate_legacy_settings so
    metadata_sources is populated like a real running install."""
    from app.metadata.source_config import migrate_legacy_settings
    p = tmp_path / "settings.json"
    seed = dict(config.DEFAULT_SETTINGS)
    migrate_legacy_settings(seed)
    p.write_text(json.dumps(seed))
    monkeypatch.setattr(config, "SETTINGS_PATH", p)
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


class TestAmazonFormatLanguageRoundTrip:
    """Stage 5++ — Amazon row carries `format` + `language` strings
    that the frontend dropdowns drive."""

    async def test_get_returns_amazon_defaults(self, isolated_settings):
        """Fresh install seeds amazon.format='kindle' +
        language='English' via `_DEFAULT_NEW_INSTALL_STATE` (commit 1).
        The GET endpoint should reflect that."""
        async with await _client(_make_app()) as ac:
            resp = await ac.get("/api/v1/metadata-sources")
        assert resp.status_code == 200
        payload = resp.json()
        amazon = payload["state"]["sources"]["amazon"]
        assert amazon["format"] == "kindle"
        assert amazon["language"] == "English"

    async def test_get_returns_null_for_non_amazon_sources(
        self, isolated_settings,
    ):
        """format/language are Amazon-only — every other source's
        row should have them as None."""
        async with await _client(_make_app()) as ac:
            resp = await ac.get("/api/v1/metadata-sources")
        payload = resp.json()
        for name, entry in payload["state"]["sources"].items():
            if name == "amazon":
                continue
            assert entry.get("format") is None, (
                f"{name!r} should not carry Amazon-specific format"
            )
            assert entry.get("language") is None, (
                f"{name!r} should not carry Amazon-specific language"
            )

    async def test_put_persists_amazon_format(self, isolated_settings):
        """User picks Paperback in the UI → PUT round-trips through
        the settings store and a subsequent GET returns the new
        value."""
        async with await _client(_make_app()) as ac:
            resp = await ac.get("/api/v1/metadata-sources")
            state = resp.json()["state"]
            state["sources"]["amazon"]["format"] = "paperback"
            state["sources"]["amazon"]["language"] = "Spanish"
            put = await ac.put("/api/v1/metadata-sources", json=state)
            assert put.status_code == 200, put.text
            resp2 = await ac.get("/api/v1/metadata-sources")
        amazon = resp2.json()["state"]["sources"]["amazon"]
        assert amazon["format"] == "paperback"
        assert amazon["language"] == "Spanish"

    async def test_put_accepts_all_formats(self, isolated_settings):
        """All four supported formats + the unfiltered allFormats
        value should be accepted by the PUT validator."""
        async with await _client(_make_app()) as ac:
            resp = await ac.get("/api/v1/metadata-sources")
            state = resp.json()["state"]
            for fmt in ("kindle", "paperback", "hardcover",
                        "mass_market", "allFormats"):
                state["sources"]["amazon"]["format"] = fmt
                put = await ac.put("/api/v1/metadata-sources", json=state)
                assert put.status_code == 200, (
                    f"PUT with format={fmt!r} unexpectedly rejected: "
                    f"{put.text}"
                )

    async def test_put_preserves_other_fields(self, isolated_settings):
        """Setting format/language must not corrupt the other
        per-source toggles."""
        async with await _client(_make_app()) as ac:
            resp = await ac.get("/api/v1/metadata-sources")
            state = resp.json()["state"]
            state["sources"]["amazon"]["format"] = "hardcover"
            state["sources"]["amazon"]["ebook_scan"] = False
            state["sources"]["amazon"]["rate_limit"] = 45.0
            put = await ac.put("/api/v1/metadata-sources", json=state)
            assert put.status_code == 200, put.text
            resp2 = await ac.get("/api/v1/metadata-sources")
        amazon = resp2.json()["state"]["sources"]["amazon"]
        assert amazon["format"] == "hardcover"
        assert amazon["ebook_scan"] is False
        assert amazon["rate_limit"] == 45.0
