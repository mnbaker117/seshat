"""
Tests for the v2.10.7 Google Books API-key wiring.

Pre-v2.10.7 GoogleBooksSource hit the no-key public endpoint and
kept tripping its 429 circuit-breaker (~1000 req/day quota shared
across every anonymous client on the same IP). v2.10.7 adds an
optional `api_key` constructor arg + `update_api_key()` method;
when set, every request gets `?key=…` appended via a new
`_request_params()` helper.
"""
from __future__ import annotations

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
