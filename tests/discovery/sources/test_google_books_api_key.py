"""
Tests for the v2.10.7 Google Books API-key wiring.

Pre-v2.10.7 GoogleBooksSource hit the no-key public endpoint and
kept tripping its 429 circuit-breaker (~1000 req/day quota shared
across every anonymous client on the same IP). v2.10.7 adds an
optional `api_key` constructor arg + `update_api_key()` method;
when set, every request gets `?key=…` appended via a new
`_request_params()` helper.

Extended in v2.10.10:
- API-key redaction in base-class warning logs (the URL inside
  `httpx.HTTPStatusError`'s string repr was leaking `key=…`).
- 503 retry-with-backoff while preserving 429 fail-fast quota behavior.
"""
from __future__ import annotations

import httpx
import pytest

from app.discovery.sources.base import _redact_sensitive
from app.discovery.sources.google_books import GoogleBooksSource


class TestRequestParamsKeyInjection:
    def test_no_key_yields_unchanged_params(self):
        src = GoogleBooksSource(rate_limit=0)
        out = src._request_params({"q": "foo", "maxResults": "5"})
        assert out == {"q": "foo", "maxResults": "5"}
        assert "key" not in out

    def test_key_appended_when_set(self):
        src = GoogleBooksSource(rate_limit=0, api_key="AIzaSy_test")
        out = src._request_params({"q": "foo"})
        assert out == {"q": "foo", "key": "AIzaSy_test"}

    def test_does_not_mutate_caller_dict(self):
        src = GoogleBooksSource(rate_limit=0, api_key="AIzaSy_test")
        base = {"q": "foo"}
        out = src._request_params(base)
        assert "key" not in base, "request_params must not mutate caller's dict"
        assert "key" in out

    def test_whitespace_in_key_stripped(self):
        src = GoogleBooksSource(rate_limit=0, api_key="  AIzaSy_test  ")
        assert src.api_key == "AIzaSy_test"

    def test_empty_string_key_treated_as_no_key(self):
        src = GoogleBooksSource(rate_limit=0, api_key="")
        out = src._request_params({"q": "foo"})
        assert "key" not in out


class TestUpdateApiKey:
    def test_update_changes_subsequent_param_injection(self):
        # Mirrors the lookup.py per-source pre-flight pattern:
        # the source is constructed once at startup with whatever
        # key was loaded from settings, then update_api_key() is
        # called per-scan with the freshest secrets-store value.
        src = GoogleBooksSource(rate_limit=0, api_key="old_key")
        assert src._request_params({})["key"] == "old_key"

        src.update_api_key("new_key")
        assert src._request_params({})["key"] == "new_key"

    def test_update_to_empty_disables_key_injection(self):
        src = GoogleBooksSource(rate_limit=0, api_key="some_key")
        src.update_api_key("")
        assert "key" not in src._request_params({"q": "foo"})

    def test_update_strips_whitespace(self):
        src = GoogleBooksSource(rate_limit=0)
        src.update_api_key("   AIzaSy_padded   ")
        assert src.api_key == "AIzaSy_padded"

    def test_update_with_none_safe(self):
        src = GoogleBooksSource(rate_limit=0, api_key="old")
        src.update_api_key(None)  # type: ignore[arg-type]
        assert src.api_key == ""


class TestRedactSensitive:
    """v2.10.10: `_redact_sensitive` strips API keys / tokens from
    strings (typically exception repr) before they hit log output.
    httpx exception messages include the full request URL with
    params, which would otherwise leak the Google Books `key=`
    value into operator-visible WARN logs."""

    def test_key_param_redacted(self):
        url = "https://www.googleapis.com/books/v1/volumes?q=foo&key=AIzaSy_secret123"
        out = _redact_sensitive(url)
        assert "AIzaSy_secret123" not in out
        assert "key=REDACTED" in out
        assert "q=foo" in out  # non-sensitive params preserved

    def test_token_param_redacted(self):
        s = "GET https://example.com?token=ghp_abcdef123 failed"
        out = _redact_sensitive(s)
        assert "ghp_abcdef123" not in out
        assert "token=REDACTED" in out

    def test_api_key_underscore_variant_redacted(self):
        s = "url=https://x.com?api_key=xyz789&page=2"
        out = _redact_sensitive(s)
        assert "xyz789" not in out
        assert "api_key=REDACTED" in out
        assert "page=2" in out

    def test_case_insensitive(self):
        s = "https://x.com?KEY=ABC&Token=DEF"
        out = _redact_sensitive(s)
        assert "ABC" not in out
        assert "DEF" not in out

    def test_no_sensitive_params_passthrough(self):
        s = "https://example.com?q=foo&page=2"
        assert _redact_sensitive(s) == s

    def test_exception_message_redacted(self):
        # Simulate the actual leak path: httpx HTTPStatusError's str()
        # includes the full request URL with query params.
        url_with_key = "https://www.googleapis.com/books/v1/volumes?q=foo&key=AIzaSy_secret_xyz"
        msg = (
            f"Server error '503 Service Unavailable' for url '{url_with_key}'\n"
            "For more information check: https://developer.mozilla.org/..."
        )
        out = _redact_sensitive(msg)
        assert "AIzaSy_secret_xyz" not in out
        assert "key=REDACTED" in out


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class TestRetryOn503NotOn429:
    """v2.10.10: 503 (transient server error) retries with backoff;
    429 (quota exhausted) still fails fast. Pre-v2.10.10 google_books
    set `retries=0` for ALL failures, so 503 transients (seen 3× in
    the 2026-05-12 validation harness) bubbled as terminal failures."""

    async def test_429_fails_fast_no_retry(self, monkeypatch):
        src = GoogleBooksSource(rate_limit=0)
        calls = {"n": 0}

        async def fake_super_get(self, url, retries=0, **kwargs):
            calls["n"] += 1
            req = httpx.Request("GET", url)
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("429 Too Many Requests", request=req, response=resp)

        # Patch BaseSource._get (what super()._get resolves to)
        from app.discovery.sources.base import BaseSource
        monkeypatch.setattr(BaseSource, "_get", fake_super_get)

        with pytest.raises(httpx.HTTPStatusError):
            await src._get("https://example.com/api")
        assert calls["n"] == 1, "429 should not be retried"
        assert src._consecutive_429s == 1

    async def test_503_retried_then_succeeds(self, monkeypatch):
        src = GoogleBooksSource(rate_limit=0)
        calls = {"n": 0}

        async def fake_super_get(self, url, retries=0, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                req = httpx.Request("GET", url)
                resp = httpx.Response(503, request=req)
                raise httpx.HTTPStatusError("503 Service Unavailable", request=req, response=resp)
            return _FakeResponse(200)

        from app.discovery.sources.base import BaseSource
        monkeypatch.setattr(BaseSource, "_get", fake_super_get)
        # Skip actual sleep so the test is fast
        import asyncio
        async def no_sleep(_):
            return None
        monkeypatch.setattr(asyncio, "sleep", no_sleep)

        resp = await src._get("https://example.com/api")
        assert calls["n"] == 3, "503 should retry up to 2× before success"
        assert resp.status_code == 200
        # Success must reset the 429 counter
        assert src._consecutive_429s == 0

    async def test_503_exhausts_retries_then_raises(self, monkeypatch):
        src = GoogleBooksSource(rate_limit=0)
        calls = {"n": 0}

        async def fake_super_get(self, url, retries=0, **kwargs):
            calls["n"] += 1
            req = httpx.Request("GET", url)
            resp = httpx.Response(503, request=req)
            raise httpx.HTTPStatusError("503 Service Unavailable", request=req, response=resp)

        from app.discovery.sources.base import BaseSource
        monkeypatch.setattr(BaseSource, "_get", fake_super_get)
        import asyncio
        async def no_sleep(_):
            return None
        monkeypatch.setattr(asyncio, "sleep", no_sleep)

        with pytest.raises(httpx.HTTPStatusError):
            await src._get("https://example.com/api")
        assert calls["n"] == 3, "should try 3 times total (1 initial + 2 retries)"

    async def test_429_counter_resets_on_success(self, monkeypatch):
        src = GoogleBooksSource(rate_limit=0)
        src._consecutive_429s = 3  # primed from prior failures

        async def fake_super_get(self, url, retries=0, **kwargs):
            return _FakeResponse(200)

        from app.discovery.sources.base import BaseSource
        monkeypatch.setattr(BaseSource, "_get", fake_super_get)

        await src._get("https://example.com/api")
        assert src._consecutive_429s == 0, "success must reset the counter"

    async def test_network_error_retried(self, monkeypatch):
        src = GoogleBooksSource(rate_limit=0)
        calls = {"n": 0}

        async def fake_super_get(self, url, retries=0, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.ConnectError("connection refused")
            return _FakeResponse(200)

        from app.discovery.sources.base import BaseSource
        monkeypatch.setattr(BaseSource, "_get", fake_super_get)
        import asyncio
        async def no_sleep(_):
            return None
        monkeypatch.setattr(asyncio, "sleep", no_sleep)

        resp = await src._get("https://example.com/api")
        assert calls["n"] == 2
        assert resp.status_code == 200
