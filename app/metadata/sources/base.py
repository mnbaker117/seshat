"""
MetaSource base class — contract that every metadata provider must honor.

Book-centric (unlike AthenaScout's author-centric `BaseSource`): given a
title + author, return the single best match as a `MetaRecord`. This
matches Seshat's review-queue use case where we already know which
torrent we grabbed and just want metadata to enrich it.

Shared concerns that live in this base:
  - httpx.AsyncClient lifecycle (lazy-created, aclose on shutdown)
  - A rate-limited `_get()` helper that mirrors the AthenaScout
    pattern (asyncio.sleep before each request, exponential backoff
    on 5xx/network errors)
  - Hooks for test injection: subclasses read self._get / self.client
    so tests can swap in a fake transport on the httpx client.

Subclasses override:
  - `name` class attribute (used for logging + config lookups)
  - `search_book(title, author)` coroutine
  - optionally `default_headers`, `default_timeout`

Source plugins should NEVER raise for "no match" or "API returned
nothing"; return None instead. Reserve exceptions for hard protocol
errors (network down, bad response format) and let the enricher
decide whether to retry or move on.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.metadata.record import MetaRecord


class MetaSource:
    """Base class for Seshat metadata source plugins."""

    # ── override in subclasses ─────────────────────────────
    name: str = "base"
    default_headers: dict = {}
    default_timeout: float = 30.0
    follow_redirects: bool = True

    def __init__(self, *, rate_limit: float = 1.0):
        self.rate_limit = rate_limit
        self.logger = logging.getLogger(f"seshat.metadata.{self.name}")
        self._client: Optional[httpx.AsyncClient] = None

    def _build_client(self) -> httpx.AsyncClient:
        """Construct the httpx client. Override for custom auth headers."""
        return httpx.AsyncClient(
            timeout=self.default_timeout,
            headers=self.default_headers,
            follow_redirects=self.follow_redirects,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def set_client(self, client: httpx.AsyncClient) -> None:
        """Test hook: inject a pre-built httpx client with a fake transport."""
        self._client = client

    async def _get(
        self, url: str, *, retries: int = 2, **kwargs
    ) -> httpx.Response:
        """Rate-limited GET with exponential backoff on failure.

        Mirrors AthenaScout's BaseSource._get shape. Retries up to
        `retries` additional attempts with 3s → 6s → 12s waits.
        Re-raises the final exception on terminal failure so the
        caller (the enricher) can log + fall through to the next
        provider.
        """
        for attempt in range(retries + 1):
            try:
                if self.rate_limit > 0:
                    await asyncio.sleep(self.rate_limit)
                resp = await self.client.get(url, **kwargs)
                resp.raise_for_status()
                return resp
            except Exception as e:
                if attempt < retries:
                    backoff = min(3 * (2 ** attempt), 12)
                    self.logger.debug(
                        "%s: attempt %d/%d failed for %s: %s — retry in %ds",
                        self.name, attempt + 1, retries + 1, url, e, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                self.logger.warning(
                    "%s: giving up on %s after %d attempts: %s",
                    self.name, url, retries + 1, e,
                )
                raise

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    # ── abstract surface ────────────────────────────────────

    async def search_book(
        self, title: str, author: str
    ) -> Optional[MetaRecord]:
        """Search this source for a book matching title + author.

        Return the best match as a `MetaRecord`, or None if nothing
        plausible came back. The enricher scores the result; sources
        shouldn't self-score.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement search_book()"
        )
