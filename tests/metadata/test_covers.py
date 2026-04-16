"""
Cover fetcher tests using httpx.MockTransport so no real network
calls are made.
"""
import httpx
import pytest

from app.metadata.covers import fetch_cover


def _client_returning(body: bytes, *, status: int = 200, ctype: str = "image/jpeg") -> httpx.AsyncClient:
    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status,
            content=body,
            headers={"content-type": ctype},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


class TestFetchCover:
    async def test_happy_path_jpeg(self, tmp_path):
        client = _client_returning(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        path = await fetch_cover(
            "https://example.com/cover.jpg",
            dest_dir=tmp_path,
            client=client,
        )
        await client.aclose()
        assert path is not None
        assert path.exists()
        assert path.suffix == ".jpg"
        assert path.stat().st_size > 0

    async def test_extension_from_content_type(self, tmp_path):
        client = _client_returning(b"RIFF...", ctype="image/webp")
        path = await fetch_cover(
            "https://example.com/cover",
            dest_dir=tmp_path,
            client=client,
        )
        await client.aclose()
        assert path is not None
        assert path.suffix == ".webp"

    async def test_empty_url_returns_none(self, tmp_path):
        result = await fetch_cover("", dest_dir=tmp_path)
        assert result is None

    async def test_http_error_returns_none(self, tmp_path):
        client = _client_returning(b"", status=500)
        path = await fetch_cover(
            "https://example.com/boom",
            dest_dir=tmp_path,
            client=client,
        )
        await client.aclose()
        assert path is None

    async def test_oversized_rejected(self, tmp_path):
        big = b"\x00" * (9 * 1024 * 1024)  # 9 MB > 8 MB cap
        client = _client_returning(big)
        path = await fetch_cover(
            "https://example.com/huge.jpg",
            dest_dir=tmp_path,
            client=client,
        )
        await client.aclose()
        assert path is None
