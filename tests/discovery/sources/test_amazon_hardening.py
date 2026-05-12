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
        rate_sleeps = [d for d in durations if d < 8.0]
        assert len(rate_sleeps) == 5
        # All should be in [2.0, 2.5]
        for s in rate_sleeps:
            assert 2.0 <= s <= 2.5, f"sleep {s} outside jitter range"
        # And NOT all identical — proves jitter is applied
        assert len(set(rate_sleeps)) > 1, (
            "rate-limit sleeps are perfectly periodic — jitter not applied"
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
