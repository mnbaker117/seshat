"""
Tests for the v2.10.6 Amazon hardening (Phase 4 of the v2.11.0 plan).

Pre-v2.10.6 the Amazon `_fetch` was naive: rate-limit sleep, single
GET, return body on HTTP 200, return None on anything else. The Phase
0 baseline harness showed Amazon CAPTCHA-blocking mid-batch (5/14
authors worked then everything else returned None) — the absence of
explicit CAPTCHA detection meant we couldn't distinguish "genuine
miss" from "blocked at the gate," and the absence of jitter likely
made the periodicity itself a fingerprint trigger.

This file covers:
  - Pure helpers: `_is_captcha_page` / `_is_robot_check_503`
  - `_fetch` behavior: jitter applied, CAPTCHA → no retry, robot-check
    503 → no retry, genuine 5xx → one retry with backoff
"""
from __future__ import annotations

import httpx

from app.discovery.sources.amazon import (
    AmazonSource,
    _is_captcha_page,
    _is_robot_check_503,
)


# ── Pure helpers ──────────────────────────────────────────────────


class TestCaptchaDetection:
    def test_classic_captcha_marker(self):
        body = """
        <html><body>
        <h4>Enter the characters you see below</h4>
        <p>Sorry, we just need to make sure you're not a robot.</p>
        </body></html>
        """
        assert _is_captcha_page(body) is True

    def test_validate_captcha_url_marker(self):
        body = '<form action="/errors/validateCaptcha">...</form>'
        assert _is_captcha_page(body) is True

    def test_alternate_phrasing(self):
        assert _is_captcha_page(
            "Type the characters you see in this image"
        ) is True

    def test_normal_product_page_not_captcha(self):
        body = '<html><body><h1 id="productTitle">Storm Front</h1></body></html>'
        assert _is_captcha_page(body) is False

    def test_empty_body_not_captcha(self):
        assert _is_captcha_page("") is False
        assert _is_captcha_page(None) is False  # type: ignore[arg-type]


class TestRobotCheck503Detection:
    def test_503_with_robot_check_body(self):
        assert _is_robot_check_503(503, "<title>Robot Check</title>") is True

    def test_503_with_automated_access_marker(self):
        body = "Sorry! To discuss automated access to Amazon data please contact..."
        assert _is_robot_check_503(503, body) is True

    def test_503_with_normal_5xx_body_not_robot_check(self):
        # Genuine upstream 503 — should NOT be classified as soft-block
        # so the caller can retry it.
        assert _is_robot_check_503(503, "Service Temporarily Unavailable") is False

    def test_500_with_robot_check_text_not_classified(self):
        # The classifier is 503-specific. A 500 with the text is
        # logically suspicious but we treat it as a regular 5xx.
        assert _is_robot_check_503(500, "Robot Check") is False

    def test_200_with_robot_text_not_classified(self):
        # That's a CAPTCHA page (different code path). The 503-specific
        # classifier should not fire.
        assert _is_robot_check_503(200, "Robot Check") is False


# ── _fetch behavior ───────────────────────────────────────────────


def _patch_get(src: AmazonSource, responses):
    """Patch `src.client.get` to return canned httpx.Responses.

    `responses` is a list — each call consumes the next entry.
    """
    state = {"i": 0, "calls": []}

    async def fake_get(url, params=None, **kwargs):  # noqa: ARG001
        state["calls"].append(url)
        idx = state["i"]
        state["i"] = idx + 1
        if idx >= len(responses):
            return httpx.Response(200, content=b"")
        return responses[idx]

    # The base property `client` is computed via `_get_client()` which
    # caches into `_client`. Build the client once, then swap its
    # `get` method.
    actual_client = src.client
    actual_client.get = fake_get  # type: ignore[method-assign]
    return state


def _patch_sleep(monkeypatch):
    """No-op sleep for fast tests; record durations for jitter check."""
    durations: list[float] = []

    async def fake_sleep(d):
        durations.append(d)

    monkeypatch.setattr("app.discovery.sources.amazon.asyncio.sleep", fake_sleep)
    return durations


class TestFetchHardening:
    async def test_normal_200_returns_body(self, monkeypatch):
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        _patch_get(src, [
            httpx.Response(200, content=b"<html><body>real content</body></html>"),
        ])

        body = await src._fetch("https://www.amazon.com/s")

        assert body is not None
        assert "real content" in body
        await src.close()

    async def test_captcha_200_returns_none_no_retry(self, monkeypatch):
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        captcha_html = (
            "<html><body>Enter the characters you see below</body></html>"
        )
        state = _patch_get(src, [
            httpx.Response(200, content=captcha_html.encode()),
            # If the code retries, this 200 would mask the bug
            httpx.Response(200, content=b"<html>real</html>"),
        ])

        body = await src._fetch("https://www.amazon.com/s")

        assert body is None
        # Critical: only one HTTP call — no retry on CAPTCHA
        assert len(state["calls"]) == 1, (
            "CAPTCHA detection must not trigger a retry"
        )
        await src.close()

    async def test_robot_check_503_returns_none_no_retry(self, monkeypatch):
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        state = _patch_get(src, [
            httpx.Response(503, content=b"<title>Robot Check</title>"),
            httpx.Response(200, content=b"<html>real</html>"),
        ])

        body = await src._fetch("https://www.amazon.com/s")

        assert body is None
        assert len(state["calls"]) == 1, (
            "Robot-check 503 must not trigger a retry"
        )
        await src.close()

    async def test_genuine_5xx_retries_once(self, monkeypatch):
        durations = _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        state = _patch_get(src, [
            httpx.Response(503, content=b"Service Temporarily Unavailable"),
            httpx.Response(200, content=b"<html><body>recovered</body></html>"),
        ])

        body = await src._fetch("https://www.amazon.com/s")

        assert body is not None
        assert "recovered" in body
        # Two GET calls: original + 1 retry
        assert len(state["calls"]) == 2
        # And there was an 8s backoff sleep between them (in addition
        # to the initial rate-limit sleep). Look for it.
        assert 8.0 in durations, f"expected 8s backoff sleep, got {durations}"
        await src.close()

    async def test_genuine_5xx_retry_also_503_returns_none(self, monkeypatch):
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        state = _patch_get(src, [
            httpx.Response(503, content=b"Service Temporarily Unavailable"),
            httpx.Response(503, content=b"Still down"),
        ])

        body = await src._fetch("https://www.amazon.com/s")

        assert body is None
        assert len(state["calls"]) == 2
        await src.close()

    async def test_retry_returns_captcha_still_returns_none(self, monkeypatch):
        # The retry path must also CAPTCHA-detect the second response —
        # Amazon sometimes flips from 503 to 200-CAPTCHA between calls.
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        state = _patch_get(src, [
            httpx.Response(503, content=b"Service Temporarily Unavailable"),
            httpx.Response(200, content=b"Enter the characters you see below"),
        ])

        body = await src._fetch("https://www.amazon.com/s")

        assert body is None
        assert len(state["calls"]) == 2
        await src.close()

    async def test_404_returns_none_no_retry(self, monkeypatch):
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        state = _patch_get(src, [
            httpx.Response(404, content=b"Not Found"),
            httpx.Response(200, content=b"<html>real</html>"),
        ])

        body = await src._fetch("https://www.amazon.com/dp/B0XYZNONEXISTENT")

        assert body is None
        assert len(state["calls"]) == 1, (
            "4xx is not retryable — single attempt only"
        )
        await src.close()

    async def test_jitter_applied_to_rate_limit(self, monkeypatch):
        # Verify the rate-limit sleep is randomized (not exactly
        # `self.rate_limit`). Run 5 fetches and check the recorded
        # initial sleeps span a meaningful range.
        durations = _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=2.0)
        _patch_get(src, [
            httpx.Response(200, content=b"<html><body>ok</body></html>"),
        ] * 5)

        for _ in range(5):
            await src._fetch("https://www.amazon.com/s")

        # The first sleep on each fetch is the jittered rate-limit;
        # subsequent sleeps (if any) are the 8s backoff which we
        # don't expect to fire on a 200. So all 5 recorded sleeps
        # are jittered rate-limits.
        # v2.11.0: jitter widened from fixed `uniform(0, 0.5)` to
        # proportional `uniform(0, rate*0.5)`. At rate=2.0 the range
        # is [2.0, 3.0]. At rate=30.0 (the new default) it would be
        # [30.0, 45.0]. Floor of 0.5s for the jitter max so tiny
        # rates still get some variance.
        rate_sleeps = [d for d in durations if d < 8.0]
        assert len(rate_sleeps) == 5
        # All should be in [2.0, 3.0] (proportional jitter at rate=2)
        for s in rate_sleeps:
            assert 2.0 <= s <= 3.0, f"sleep {s} outside jitter range"
        # And NOT all identical — proves jitter is applied
        assert len(set(rate_sleeps)) > 1, (
            "rate-limit sleeps are perfectly periodic — jitter not applied"
        )
        await src.close()

    async def test_jitter_scales_with_rate_limit(self, monkeypatch):
        """v2.11.0 — jitter is proportional to rate (max = rate * 0.5).

        At rate=30 (the new Amazon discovery default) the effective
        spacing varies 30-45s; at rate=2 (legacy) it's 2-3s. Proves the
        jitter scales correctly with the user-configured rate, so the
        cadence doesn't pattern-match `rate.0s` exactly regardless
        of how slow or fast the user sets it.
        """
        durations = _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=30.0)
        _patch_get(src, [
            httpx.Response(200, content=b"<html><body>ok</body></html>"),
        ] * 4)

        for _ in range(4):
            await src._fetch("https://www.amazon.com/s")

        rate_sleeps = [d for d in durations if d < 60.0]
        assert len(rate_sleeps) == 4
        # All in [30.0, 45.0] = rate + uniform(0, rate*0.5)
        for s in rate_sleeps:
            assert 30.0 <= s <= 45.0, f"sleep {s} outside [30, 45] jitter range"
        # And not all identical
        assert len(set(rate_sleeps)) > 1
        await src.close()

    async def test_curl_cffi_session_used_when_available(self, monkeypatch):
        """v2.11.0 — when curl_cffi is installed, `_fetch` prefers the
        impersonating AsyncSession over the base-class httpx client.
        Mocks `_get_cf_session` to return a fake session and asserts
        it's the one that handled the GET."""
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)

        called = {"cf": 0, "httpx": 0}

        class _FakeResp:
            status_code = 200
            text = "<html><body>cf result</body></html>"

        async def fake_cf_get(url, params=None, **kwargs):
            called["cf"] += 1
            return _FakeResp()

        class _FakeSession:
            get = staticmethod(fake_cf_get)

            async def close(self):
                pass

        monkeypatch.setattr(
            AmazonSource, "_get_cf_session", lambda self: _FakeSession(),
        )
        # Also patch the httpx client.get so if the fallback fires we know
        async def fake_httpx_get(url, params=None, **kwargs):
            called["httpx"] += 1
            return httpx.Response(200, content=b"<html>httpx</html>")
        actual_client = src.client
        actual_client.get = fake_httpx_get  # type: ignore[method-assign]

        body = await src._fetch("https://www.amazon.com/s")

        assert body is not None
        assert "cf result" in body
        assert called["cf"] == 1, "curl_cffi session should have been called"
        assert called["httpx"] == 0, "httpx fallback should NOT fire when cf_session is available"
        await src.close()

    async def test_falls_back_to_httpx_when_curl_cffi_unavailable(self, monkeypatch):
        """When `_get_cf_session` returns None (curl_cffi not installed),
        `_fetch` falls back to the base-class httpx client. This preserves
        the source's ability to import + run in dev environments without
        the optional binary dep."""
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        # Force the fallback path
        monkeypatch.setattr(AmazonSource, "_get_cf_session", lambda self: None)

        called = {"httpx": 0}
        async def fake_httpx_get(url, params=None, **kwargs):
            called["httpx"] += 1
            return httpx.Response(200, content=b"<html>fallback</html>")
        actual_client = src.client
        actual_client.get = fake_httpx_get  # type: ignore[method-assign]

        body = await src._fetch("https://www.amazon.com/s")

        assert body is not None
        assert "fallback" in body
        assert called["httpx"] == 1
        await src.close()

    async def test_close_releases_curl_cffi_session(self, monkeypatch):
        """`close()` must release the curl_cffi session — leaking
        AsyncSessions across container lifetime would accumulate
        TLS state at Akamai's edge."""
        src = AmazonSource(rate_limit=0)

        closed = {"cf": False}

        class _FakeSession:
            async def close(self):
                closed["cf"] = True

        fake = _FakeSession()
        src._cf_session = fake  # bypass lazy init

        await src.close()

        assert closed["cf"] is True
        assert src._cf_session is None

    async def test_jitter_floor_for_tiny_rate(self, monkeypatch):
        """Tiny rate (or 0) still gets some jitter, not zero. Floor
        of 0.5s on the jitter max prevents perfectly periodic
        cadence at low rates."""
        durations = _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        _patch_get(src, [
            httpx.Response(200, content=b"<html><body>ok</body></html>"),
        ] * 4)

        for _ in range(4):
            await src._fetch("https://www.amazon.com/s")

        rate_sleeps = [d for d in durations if d < 8.0]
        assert len(rate_sleeps) == 4
        # All in [0.0, 0.5] — jitter floor preserves variance
        for s in rate_sleeps:
            assert 0.0 <= s <= 0.5
        assert len(set(rate_sleeps)) > 1
        await src.close()

    async def test_thin_200_body_logs_warning(self, monkeypatch, caplog):
        """v2.10.8 — a 200 with sub-50KB body is suspicious because
        real Amazon search/detail pages are 500KB+. Log a WARNING so
        operators can correlate "amazon: no results" with actual
        upstream weirdness instead of silently shrugging it off."""
        import logging
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        # 5 KB body — well under the 50 KB threshold, but no CAPTCHA
        # marker in the content so the existing detector won't fire.
        thin_body = b"<html><body>" + b"x" * 5000 + b"</body></html>"
        _patch_get(src, [httpx.Response(200, content=thin_body)])

        with caplog.at_level(logging.WARNING, logger="seshat.discovery.amazon"):
            body = await src._fetch("https://www.amazon.com/s")

        assert body is not None  # not a hard error — body is returned
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("suspiciously small body" in r.message for r in warnings), (
            f"expected thin-body WARNING; got: {[r.message for r in warnings]}"
        )
        await src.close()

    async def test_normal_size_200_no_warning(self, monkeypatch, caplog):
        """A normal-sized response should NOT trigger the thin-body
        warning — only the suspicious sub-50KB ones."""
        import logging
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)
        # 100 KB body — comfortably above the threshold.
        normal_body = b"<html><body>" + b"x" * 100_000 + b"</body></html>"
        _patch_get(src, [httpx.Response(200, content=normal_body)])

        with caplog.at_level(logging.WARNING, logger="seshat.discovery.amazon"):
            await src._fetch("https://www.amazon.com/s")

        warnings = [r for r in caplog.records if "suspiciously small" in r.message]
        assert warnings == [], (
            f"thin-body warning fired on a normal-sized response: "
            f"{[r.message for r in warnings]}"
        )
        await src.close()

    async def test_network_error_returns_none(self, monkeypatch):
        _patch_sleep(monkeypatch)
        src = AmazonSource(rate_limit=0)

        async def fake_get(url, params=None, **kwargs):  # noqa: ARG001
            raise httpx.ConnectError("connection refused")

        # Force-build the client first so we can swap its method
        actual_client = src.client
        actual_client.get = fake_get  # type: ignore[method-assign]

        body = await src._fetch("https://www.amazon.com/s")

        assert body is None
        await src.close()
