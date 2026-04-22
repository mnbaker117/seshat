"""
ntfy notification sender.

Sends push notifications via ntfy.sh (or a self-hosted ntfy server)
for significant events: scan completions, new books found, MAM matches.
No-op when ntfy_url is empty — callers don't need to check config.

Two delivery modes, switched per-user:
  - Per-event (default): each event sends immediately
  - Digest: events are queued in memory and flushed on a daily/weekly
    cadence by app.digest.flush_digest()

The per-event API stays the same in either mode — call sites are
agnostic. Digest mode is implemented by routing send() through an
in-memory queue when ntfy_digest_enabled is True.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import load_settings

logger = logging.getLogger("seshat.discovery.notify")

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        finally:
            _client = None


# ─── Digest queue ───────────────────────────────────────────
# When ntfy_digest_enabled is True, event-specific senders enqueue
# their (title, message) here instead of pushing to ntfy. The
# scheduler in app.digest periodically drains and consolidates.

@dataclass
class DigestEvent:
    kind: str  # "scan_complete", "new_books", "mam", "pipeline", "library", "cookie"
    title: str
    message: str
    at: float = field(default_factory=time.time)


_digest_queue: list[DigestEvent] = []
_digest_lock = asyncio.Lock()


async def enqueue_digest(event: DigestEvent) -> None:
    async with _digest_lock:
        _digest_queue.append(event)


async def drain_digest() -> list[DigestEvent]:
    """Pop and return all queued events. Caller is responsible for
    formatting + sending the consolidated digest."""
    async with _digest_lock:
        events = list(_digest_queue)
        _digest_queue.clear()
        return events


def digest_size() -> int:
    return len(_digest_queue)


def _resolve_endpoint(url: str, topic: str) -> Optional[str]:
    """Resolve full ntfy endpoint from user settings.

    Accepts: "https://ntfy.sh" + topic, "ntfy.sh/mytopic", etc.
    """
    if not url or not url.strip():
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.path and parsed.path != "/":
        return url.rstrip("/")
    if not topic or not topic.strip():
        return None
    return f"{url.rstrip('/')}/{topic.strip()}"


async def send(
    *,
    title: str,
    message: str,
    priority: int = 3,
    tags: Optional[list[str]] = None,
) -> bool:
    """Send a notification via ntfy. Returns True on success.

    Reads ntfy_url and ntfy_topic from settings. No-op if not configured.
    Bypasses the digest queue — for digest-aware sends use the
    event-specific helpers below.
    """
    s = load_settings()
    endpoint = _resolve_endpoint(s.get("ntfy_url", ""), s.get("ntfy_topic", ""))
    if not endpoint:
        return False

    headers = {"Title": title, "Priority": str(priority)}
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        resp = await _get_client().post(
            endpoint, content=message.encode("utf-8"), headers=headers,
        )
        if resp.status_code == 200:
            logger.debug(f"ntfy sent: {title}")
            return True
        logger.warning(f"ntfy HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception:
        logger.debug("ntfy send failed", exc_info=True)
        return False


async def _emit(
    *,
    kind: str,
    title: str,
    message: str,
    priority: int = 3,
    tags: Optional[list[str]] = None,
) -> bool:
    """Either send immediately or enqueue for digest, based on settings."""
    s = load_settings()
    if s.get("ntfy_digest_enabled"):
        await enqueue_digest(DigestEvent(kind=kind, title=title, message=message))
        return True
    return await send(title=title, message=message, priority=priority, tags=tags)


# ─── Event-specific senders ─────────────────────────────────

async def notify_scan_complete(
    *, label: str, new_books: int, authors_total: int = 1,
) -> bool:
    """Source-scan finished. `label` is "Author Name" for single-author
    scans, or a scan-type label like "Bulk Author Scan" otherwise.
    No-op if `new_books` is zero."""
    if new_books <= 0:
        return False
    s = load_settings()
    if not s.get("ntfy_on_scan_complete", True):
        return False
    if authors_total <= 1:
        title = f"Scan complete: {label}"
        message = f"{new_books} new book(s) found"
    else:
        title = f"{label} complete"
        message = f"{new_books} new book(s) across {authors_total} author(s)"
    return await _emit(
        kind="scan_complete", title=title, message=message,
        tags=["books", "mag"],
    )


async def notify_new_books(author_name: str, count: int) -> bool:
    """Per-author "new books found" within a bulk scan. Useful when the
    user wants per-author granularity in addition to the summary."""
    if count <= 0:
        return False
    s = load_settings()
    if not s.get("ntfy_on_new_books", True):
        return False
    return await _emit(
        kind="new_books",
        title=f"New books: {author_name}",
        message=f"{count} new book(s) discovered",
        tags=["books", "sparkles"],
    )


async def notify_mam_scan_complete(
    scanned: int, found: int, possible: int, not_found: int,
) -> bool:
    s = load_settings()
    if not s.get("ntfy_on_mam_complete", True):
        return False
    return await _emit(
        kind="mam",
        title="MAM scan complete",
        message=(
            f"Scanned {scanned} books\n"
            f"Found: {found} · Possible: {possible} · Not found: {not_found}"
        ),
        tags=["mag"],
    )


async def notify_pipeline_sent(sent: int, skipped: int) -> bool:
    if sent <= 0:
        return False
    s = load_settings()
    if not s.get("ntfy_on_pipeline_sent", True):
        return False
    return await _emit(
        kind="pipeline",
        title=f"Sent {sent} book(s) to pipeline",
        message=f"{sent} queued for download" + (f", {skipped} skipped" if skipped else ""),
        tags=["arrow_down", "books"],
    )


async def notify_library_sync(library_name: str, new: int, updated: int) -> bool:
    s = load_settings()
    if not s.get("ntfy_on_library_sync", False):
        return False
    if new == 0 and updated == 0:
        return False
    return await _emit(
        kind="library",
        title=f"Library synced: {library_name}",
        message=f"{new} new, {updated} updated",
        tags=["books"],
    )


async def notify_mam_cookie_rotated() -> bool:
    s = load_settings()
    if not s.get("ntfy_on_mam_cookie_rotated", False):
        return False
    return await _emit(
        kind="cookie",
        title="MAM cookie rotated",
        message="Session token automatically refreshed",
        priority=2,
        tags=["key"],
    )
