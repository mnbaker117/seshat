"""
MAM user-status API.

`get_user_status()` hits MAM's `jsonLoad.php` endpoint, which returns
the authenticated user's current ratio, wedge count, class name,
seedbonus, and upload/download totals. The policy engine uses these
values to make per-announce decisions:

  - ratio → should we spend download credit on this torrent?
  - wedges → can we apply a freeleech wedge to avoid ratio cost?
  - classname → does "VIP" appear in the class name (affects some
    site-level perks, though the per-torrent VIP flag is separate)?
  - seedbonus → how many bonus points are available to buy more wedges?

The response is cached in-memory for a configurable TTL (default 5
minutes) so that a burst of announces doesn't hammer MAM with
redundant user-status lookups.

Routes through `cookie._do_get` so cookie auto-rotation fires on
every response.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from app.mam.cookie import _do_get

_log = logging.getLogger("seshat.mam")

MAM_USER_URL = "https://www.myanonamouse.net/jsonLoad.php"

# Default cache TTL in seconds (5 minutes).
_CACHE_TTL = 300


@dataclass(frozen=True)
class UserStatus:
    """Snapshot of the authenticated MAM user's account status."""

    ratio: float
    wedges: int
    seedbonus: int
    classname: str
    username: str
    uid: int
    uploaded_bytes: int
    downloaded_bytes: int


# ─── In-memory cache ────────────────────────────────────────

_cache: dict[str, tuple[float, UserStatus]] = {}


def _cache_key(token: str) -> str:
    """Derive a cache key from the token (first 16 chars for privacy)."""
    return token[:16] if token else ""


def invalidate_cache() -> None:
    """Clear the user-status cache (e.g. after a cookie rotation)."""
    _cache.clear()


# ─── Public API ─────────────────────────────────────────────


async def get_user_status(
    token: Optional[str] = None,
    ttl: int = _CACHE_TTL,
) -> UserStatus:
    """Fetch the current user's MAM account status.

    Returns a cached result if one exists within `ttl` seconds.
    Raises `UserStatusError` on any failure.

    Args:
        token: Explicit mam_id cookie value. If None, uses the
               module-level current token from cookie.py.
        ttl: Cache lifetime in seconds. Pass 0 to force a fresh fetch.
    """
    key = _cache_key(token or "")
    now = time.monotonic()

    if ttl > 0 and key in _cache:
        cached_at, cached_status = _cache[key]
        if now - cached_at < ttl:
            _log.debug("user_status cache hit (age %.0fs)", now - cached_at)
            return cached_status

    _log.info("Fetching MAM user status from jsonLoad.php")

    try:
        resp = await _do_get(MAM_USER_URL, token=token, timeout=15)
    except Exception as exc:
        raise UserStatusError(f"network error: {exc}") from exc

    if resp.status_code != 200:
        raise UserStatusError(f"HTTP {resp.status_code} from jsonLoad.php")

    try:
        data = resp.json()
    except Exception as exc:
        body_preview = resp.text[:200] if resp.text else "(empty)"
        raise UserStatusError(
            f"invalid JSON from jsonLoad.php: {body_preview}"
        ) from exc

    # jsonLoad.php returns an object on success. If it returns a bare
    # string or list, the cookie is probably invalid.
    if not isinstance(data, dict):
        raise UserStatusError(f"unexpected response shape: {type(data).__name__}")

    try:
        status = UserStatus(
            ratio=float(data.get("ratio", 0)),
            wedges=int(data.get("wedges", 0)),
            seedbonus=int(data.get("seedbonus", 0)),
            classname=str(data.get("classname", "")),
            username=str(data.get("username", "")),
            uid=int(data.get("uid", 0)),
            uploaded_bytes=int(data.get("uploaded_bytes", 0)),
            downloaded_bytes=int(data.get("downloaded_bytes", 0)),
        )
    except (ValueError, TypeError) as exc:
        raise UserStatusError(f"failed to parse user status fields: {exc}") from exc

    _cache[key] = (now, status)
    _log.info(
        "MAM user status: %s (ratio=%.1f, wedges=%d, class=%s)",
        status.username,
        status.ratio,
        status.wedges,
        status.classname,
    )
    return status


class UserStatusError(Exception):
    """Raised when the user-status fetch fails."""
