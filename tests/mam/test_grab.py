"""
Unit tests for the .torrent grab path.

Covers the full hybrid status-then-body-sniff classification matrix
from `app/mam/grab.py`. The fake-MAM HTTP fixture lets us drive every
edge case in isolation, including the gnarly "HTTP 200 + HTML body"
case that was the original motivation for sniffing the body at all.

The success path uses the minimal-but-valid bencoded torrent body
defined in `tests/fake_mam.MINIMAL_BENCODED_TORRENT`.
"""
from app.mam.grab import GrabResult, _classify_response, fetch_torrent
from tests.fake_mam import HTML_LOGIN_PAGE, MINIMAL_BENCODED_TORRENT


# ─── Success path ────────────────────────────────────────────


class TestFetchTorrentSuccess:
    async def test_happy_path_returns_torrent_bytes(self, fake_mam):
        result = await fetch_torrent("1233592", "good_token")
        assert result.success is True
        assert result.torrent_bytes == MINIMAL_BENCODED_TORRENT
        assert result.failure_kind is None
        assert result.http_status == 200

    async def test_request_uses_correct_download_url(self, fake_mam):
        await fetch_torrent("1233592", "good_token")
        assert any(
            "download.php" in str(req.url) and "tid=1233592" in str(req.url)
            for req in fake_mam.requests
        )

    async def test_request_attaches_cookie(self, fake_mam):
        await fetch_torrent("1233592", "specific_session_value")
        assert "specific_session_value" in fake_mam.cookies_seen()


# ─── Cookie-expired failure modes (the three flavors) ────────


class TestFetchTorrentCookieExpired:
    async def test_html_login_page_with_200_status(self, fake_mam):
        # The single nastiest case — MAM serves HTML with HTTP 200.
        # Status alone would call this success; only the body sniff
        # catches it.
        fake_mam.download.status = 200
        fake_mam.download.body = HTML_LOGIN_PAGE
        fake_mam.download.headers = {"content-type": "text/html"}

        result = await fetch_torrent("1233592", "expired_token")
        assert result.success is False
        assert result.failure_kind == "cookie_expired"
        assert "html" in result.failure_detail.lower()
        assert result.http_status == 200

    async def test_empty_200_body(self, fake_mam):
        fake_mam.download.body = b""
        result = await fetch_torrent("1233592", "expired_token")
        assert result.success is False
        assert result.failure_kind == "cookie_expired"
        assert "empty" in result.failure_detail.lower()

    async def test_http_403(self, fake_mam):
        fake_mam.download.status = 403
        fake_mam.download.body = b"forbidden"
        result = await fetch_torrent("1233592", "rejected_token")
        assert result.success is False
        assert result.failure_kind == "cookie_expired"
        assert result.http_status == 403

    async def test_http_401(self, fake_mam):
        fake_mam.download.status = 401
        fake_mam.download.body = b"unauthorized"
        result = await fetch_torrent("1233592", "no_auth")
        assert result.success is False
        assert result.failure_kind == "cookie_expired"
        assert result.http_status == 401

    async def test_no_token_short_circuits_as_cookie_expired(self):
        # No HTTP fixture used — empty token must short-circuit
        # before any network I/O. Treated as cookie_expired so the
        # cookie-rotation retry flow picks it up automatically.
        result = await fetch_torrent("1233592", "")
        assert result.success is False
        assert result.failure_kind == "cookie_expired"


# ─── torrent_not_found failures ──────────────────────────────


class TestFetchTorrentNotFound:
    async def test_http_404(self, fake_mam):
        fake_mam.download.status = 404
        fake_mam.download.body = b"not found"
        result = await fetch_torrent("1233592", "good_token")
        assert result.success is False
        assert result.failure_kind == "torrent_not_found"
        assert result.http_status == 404

    async def test_http_410(self, fake_mam):
        fake_mam.download.status = 410
        result = await fetch_torrent("1233592", "good_token")
        assert result.success is False
        assert result.failure_kind == "torrent_not_found"


# ─── unknown / transient failures ────────────────────────────


class TestFetchTorrentUnknown:
    async def test_http_500_marked_unknown(self, fake_mam):
        fake_mam.download.status = 500
        result = await fetch_torrent("1233592", "good_token")
        assert result.success is False
        assert result.failure_kind == "unknown"
        assert "server error" in result.failure_detail.lower()

    async def test_http_503_marked_unknown(self, fake_mam):
        fake_mam.download.status = 503
        result = await fetch_torrent("1233592", "good_token")
        assert result.success is False
        assert result.failure_kind == "unknown"

    async def test_unrecognized_200_body_not_treated_as_success(self, fake_mam):
        # Status 200, non-empty, non-HTML, non-bencode. Could be
        # anything — a JSON error, a plain-text rejection, an
        # unexpected MAM response shape. Mark unknown rather than
        # silently treating as success.
        fake_mam.download.body = b'{"error":"weird unexpected payload"}'
        result = await fetch_torrent("1233592", "good_token")
        assert result.success is False
        assert result.failure_kind == "unknown"

    async def test_empty_torrent_id(self):
        result = await fetch_torrent("", "good_token")
        assert result.success is False
        assert result.failure_kind == "unknown"
        assert "empty torrent_id" in result.failure_detail.lower()


# ─── _classify_response unit tests (no HTTP needed) ──────────
# These exercise the body-sniffing branches in isolation so
# regressions in the matcher show up in fast, offline tests.


class TestClassifyResponse:
    def _make_response(self, status: int, body: bytes) -> "object":
        # Minimal stand-in: anything with .status_code and .content works.
        class _R:
            def __init__(self, s, b):
                self.status_code = s
                self.content = b
        return _R(status, body)

    def test_bencode_d_prefix_is_success(self):
        result = _classify_response(
            "1", self._make_response(200, MINIMAL_BENCODED_TORRENT)
        )
        assert result.success is True
        assert result.torrent_bytes == MINIMAL_BENCODED_TORRENT

    def test_html_doctype_is_cookie_expired(self):
        body = b"<!DOCTYPE html>\n<html>...login...</html>"
        result = _classify_response("1", self._make_response(200, body))
        assert result.success is False
        assert result.failure_kind == "cookie_expired"

    def test_html_with_leading_whitespace_still_caught(self):
        # Defensive — if MAM ever serves a leading newline before <html>
        body = b"\n\n<html>login</html>"
        result = _classify_response("1", self._make_response(200, body))
        assert result.success is False
        assert result.failure_kind == "cookie_expired"

    def test_uppercase_html_caught(self):
        body = b"<HTML>login</HTML>"
        result = _classify_response("1", self._make_response(200, body))
        assert result.success is False
        assert result.failure_kind == "cookie_expired"
