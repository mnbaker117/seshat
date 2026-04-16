"""
Unit tests for the MAM torrent-info lookup (search API by ID).

Covers VIP/free/fl_vip parsing, caching, error handling, and the
boolean coercion logic for MAM's mixed string/int/bool representations.
All requests go through the FakeMAM fixture.
"""
import json

import pytest

from app.mam.torrent_info import (
    TorrentInfo,
    TorrentInfoError,
    _to_bool,
    get_torrent_info,
    invalidate_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure each test starts with a clean torrent-info cache."""
    invalidate_cache()
    yield
    invalidate_cache()


def _make_search_response(overrides: dict | None = None) -> bytes:
    """Build a realistic search-by-id response with optional field overrides."""
    item = {
        "id": "965093",
        "language": "1",
        "main_cat": "14",
        "category": "63",
        "catname": "Ebooks - Fantasy",
        "size": "5242880",
        "numfiles": "1",
        "vip": "0",
        "free": "0",
        "fl_vip": "0",
        "personal_freeleech": "0",
        "title": "Test Book",
        "name": "Test Book",
        "seeders": "5",
        "leechers": "0",
        "times_completed": "42",
    }
    if overrides:
        item.update(overrides)
    return json.dumps({
        "perpage": 1,
        "start": 0,
        "found": 1,
        "data": [item],
    }).encode()


# ─── Happy path ─────────────────────────────────────────────


class TestGetTorrentInfoSuccess:
    async def test_parses_regular_torrent(self, fake_mam):
        fake_mam.search.body = _make_search_response()
        info = await get_torrent_info("965093", token="tok")
        assert isinstance(info, TorrentInfo)
        assert info.torrent_id == "965093"
        assert info.vip is False
        assert info.free is False
        assert info.fl_vip is False
        assert info.personal_freeleech is False
        assert info.category == "Ebooks - Fantasy"
        assert info.title == "Test Book"

    async def test_parses_vip_torrent(self, fake_mam):
        fake_mam.search.body = _make_search_response({
            "vip": "1", "fl_vip": "1",
        })
        info = await get_torrent_info("965093", token="tok")
        assert info.vip is True
        assert info.fl_vip is True
        assert info.free is False

    async def test_parses_freeleech_torrent(self, fake_mam):
        fake_mam.search.body = _make_search_response({
            "free": "1", "fl_vip": "1",
        })
        info = await get_torrent_info("965093", token="tok")
        assert info.vip is False
        assert info.free is True
        assert info.fl_vip is True

    async def test_parses_personal_freeleech(self, fake_mam):
        fake_mam.search.body = _make_search_response({
            "personal_freeleech": "1",
        })
        info = await get_torrent_info("965093", token="tok")
        assert info.personal_freeleech is True

    async def test_request_hits_search_endpoint(self, fake_mam):
        fake_mam.search.body = _make_search_response()
        await get_torrent_info("965093", token="tok")
        assert any(
            "loadSearchJSONbasic.php" in str(req.url)
            for req in fake_mam.requests
        )


# ─── Caching ────────────────────────────────────────────────


class TestTorrentInfoCaching:
    async def test_second_call_uses_cache(self, fake_mam):
        fake_mam.search.body = _make_search_response()
        await get_torrent_info("965093", token="tok")
        await get_torrent_info("965093", token="tok")
        search_requests = [
            r for r in fake_mam.requests
            if "loadSearchJSONbasic.php" in str(r.url)
        ]
        assert len(search_requests) == 1

    async def test_different_ids_not_cached_together(self, fake_mam):
        fake_mam.search.body = _make_search_response()
        await get_torrent_info("111", token="tok")
        await get_torrent_info("222", token="tok")
        search_requests = [
            r for r in fake_mam.requests
            if "loadSearchJSONbasic.php" in str(r.url)
        ]
        assert len(search_requests) == 2

    async def test_ttl_zero_bypasses_cache(self, fake_mam):
        fake_mam.search.body = _make_search_response()
        await get_torrent_info("965093", token="tok")
        await get_torrent_info("965093", token="tok", ttl=0)
        search_requests = [
            r for r in fake_mam.requests
            if "loadSearchJSONbasic.php" in str(r.url)
        ]
        assert len(search_requests) == 2


# ─── Error handling ─────────────────────────────────────────


class TestTorrentInfoErrors:
    async def test_http_403_raises(self, fake_mam):
        fake_mam.search.status = 403
        fake_mam.search.body = b"forbidden"
        with pytest.raises(TorrentInfoError, match="HTTP 403"):
            await get_torrent_info("965093", token="bad")

    async def test_empty_body_raises(self, fake_mam):
        fake_mam.search.body = b""
        with pytest.raises(TorrentInfoError, match="empty response"):
            await get_torrent_info("965093", token="tok")

    async def test_no_results_raises(self, fake_mam):
        fake_mam.search.body = b'{"perpage":1,"start":0,"found":0,"data":[]}'
        with pytest.raises(TorrentInfoError, match="not found"):
            await get_torrent_info("999999", token="tok")


# ─── Boolean coercion ───────────────────────────────────────


class TestToBool:
    def test_string_zero(self):
        assert _to_bool("0") is False

    def test_string_one(self):
        assert _to_bool("1") is True

    def test_string_true(self):
        assert _to_bool("true") is True

    def test_int_zero(self):
        assert _to_bool(0) is False

    def test_int_one(self):
        assert _to_bool(1) is True

    def test_bool_true(self):
        assert _to_bool(True) is True

    def test_bool_false(self):
        assert _to_bool(False) is False

    def test_none(self):
        assert _to_bool(None) is False

    def test_empty_string(self):
        assert _to_bool("") is False
