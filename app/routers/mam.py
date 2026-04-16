"""
MAM user status + cookie management endpoints.

    GET  /api/v1/mam/status      — ratio, wedges, class, seedbonus,
                                    plus current cookie freshness
    POST /api/v1/mam/refresh     — force a fresh fetch (bypass cache)
    POST /api/v1/mam/validate    — run the cookie validation flow and
                                    record the result on settings.json
    POST /api/v1/mam/cookie      — emergency paste: replace the live
                                    cookie with a fresh one and validate

The status endpoint never raises on a stale or missing cookie — it
returns a payload with `cookie_configured: false` or `error: "..."` so
the UI can render an actionable banner instead of a 500 page. Useful
for the dashboard's "session expiring soon" surface.

Why this is its own router rather than living under /settings: the
operator-facing flow is "see ratio + wedges + run a validation",
which is a different mental model from "edit configuration values".
Keeping them split lets the dashboard hit `/api/v1/mam/status` on a
poll without colliding with settings reads.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.config import load_settings, save_settings
from app.mam import cookie as mam_cookie
from app.mam.user_status import (
    UserStatusError,
    get_user_status,
    invalidate_cache,
)

_log = logging.getLogger("seshat.routers.mam")

router = APIRouter(prefix="/api/v1/mam", tags=["mam"])


class MamStatusResponse(BaseModel):
    cookie_configured: bool
    cookie_age_seconds: Optional[float] = None
    last_validated_at: Optional[str] = None
    validation_ok: bool = False
    username: Optional[str] = None
    uid: Optional[int] = None
    classname: Optional[str] = None
    ratio: Optional[float] = None
    wedges: Optional[int] = None
    seedbonus: Optional[int] = None
    uploaded_bytes: Optional[int] = None
    downloaded_bytes: Optional[int] = None
    error: Optional[str] = None


class ValidateResponse(BaseModel):
    ok: bool
    message: str


class CookieRequest(BaseModel):
    cookie: str


async def _build_status(
    *, force_refresh: bool = False
) -> MamStatusResponse:
    settings = load_settings()
    token = settings.get("mam_session_id", "") or ""
    last_validated_at = settings.get("mam_last_validated_at")
    validation_ok = bool(settings.get("mam_validation_ok"))

    if not token:
        return MamStatusResponse(
            cookie_configured=False,
            last_validated_at=last_validated_at,
            validation_ok=False,
            error="No MAM session cookie configured",
        )

    cookie_age = None
    if last_validated_at:
        try:
            cookie_age = max(
                0.0,
                time.time()
                - time.mktime(time.strptime(last_validated_at, "%Y-%m-%dT%H:%M:%S")),
            )
        except (ValueError, TypeError):
            cookie_age = None

    ttl = 0 if force_refresh else 300  # default 5min cache
    try:
        status = await get_user_status(token=token, ttl=ttl)
    except UserStatusError as e:
        return MamStatusResponse(
            cookie_configured=True,
            last_validated_at=last_validated_at,
            validation_ok=validation_ok,
            cookie_age_seconds=cookie_age,
            error=str(e),
        )

    return MamStatusResponse(
        cookie_configured=True,
        cookie_age_seconds=cookie_age,
        last_validated_at=last_validated_at,
        validation_ok=True,
        username=status.username,
        uid=status.uid,
        classname=status.classname,
        ratio=status.ratio,
        wedges=status.wedges,
        seedbonus=status.seedbonus,
        uploaded_bytes=status.uploaded_bytes,
        downloaded_bytes=status.downloaded_bytes,
    )


@router.get("/status", response_model=MamStatusResponse)
async def status() -> MamStatusResponse:
    """Cached MAM user status. ~5min freshness window."""
    return await _build_status()


@router.post("/refresh", response_model=MamStatusResponse)
async def refresh() -> MamStatusResponse:
    """Force a fresh fetch from MAM, bypassing the in-memory cache."""
    invalidate_cache()
    return await _build_status(force_refresh=True)


@router.post("/validate", response_model=ValidateResponse)
async def validate() -> ValidateResponse:
    """Run the full cookie validation (IP registration + session probe).

    Updates `mam_last_validated_at` and `mam_validation_ok` in
    settings.json based on the outcome so the dashboard banner clears
    automatically when the user re-validates after a refresh.
    """
    settings = load_settings()
    token = settings.get("mam_session_id", "") or ""

    result = await mam_cookie.validate(token)
    ok = bool(result.get("success"))
    message = str(result.get("message") or "")

    settings = dict(settings)
    settings["mam_last_validated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    settings["mam_validation_ok"] = ok
    save_settings(settings)

    return ValidateResponse(ok=ok, message=message)


@router.post("/cookie", response_model=ValidateResponse)
async def replace_cookie(body: CookieRequest) -> ValidateResponse:
    """Emergency cookie paste.

    Updates the in-memory token, persists it to settings.json, and
    runs validate() so the response carries an immediate yes/no on
    whether the new cookie works. Useful when MAM has rotated and
    Seshat's auto-rotation didn't catch it (e.g. after a long
    quiet period that exceeded the keep-alive interval).
    """
    cookie = (body.cookie or "").strip()
    if not cookie:
        raise HTTPException(400, "Cookie cannot be empty")
    if len(cookie) < 32:
        raise HTTPException(400, "Cookie looks too short to be valid")

    # Persist + load into the in-memory token immediately. Subsequent
    # MAM API calls (validate, get_user_status, future grab fetches)
    # will pick this up.
    mam_cookie.set_current_token(cookie)
    invalidate_cache()
    settings = dict(load_settings())
    settings["mam_session_id"] = cookie
    save_settings(settings)

    # Run validation and stamp the result.
    result = await mam_cookie.validate(cookie)
    ok = bool(result.get("success"))
    message = str(result.get("message") or "")

    settings = dict(load_settings())
    settings["mam_last_validated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    settings["mam_validation_ok"] = ok
    save_settings(settings)

    if ok:
        _log.info("MAM cookie replaced and validated via UI emergency paste")
    else:
        _log.warning("MAM cookie replaced via UI but validation FAILED: %s", message)
    return ValidateResponse(ok=ok, message=message)


@router.post("/test-qbit", response_model=ValidateResponse)
async def test_qbit() -> ValidateResponse:
    """Test the download client connection.

    Attempts a login using the current credentials. WARNING: the
    client may ban the IP after 5 failed attempts (default 30-min
    ban in qBittorrent).
    """
    from app import state
    if state.dispatcher is None:
        return ValidateResponse(ok=False, message="Dispatcher not initialized")
    try:
        ok = await state.dispatcher.qbit.login()
        return ValidateResponse(
            ok=ok,
            message="Connected!" if ok else "Login failed — check URL, username, and password",
        )
    except Exception as e:
        return ValidateResponse(ok=False, message=f"Connection error: {e}")


@router.post("/test-notification", response_model=ValidateResponse)
async def test_notification() -> ValidateResponse:
    """Send a test notification via ntfy to verify the topic works."""
    from app.notify import ntfy
    settings = load_settings()
    url = settings.get("ntfy_url", "") or ""
    topic = settings.get("ntfy_topic", "") or ""
    if not url:
        return ValidateResponse(ok=False, message="ntfy URL not configured")
    if not topic:
        return ValidateResponse(ok=False, message="ntfy topic not configured")
    ok = await ntfy.send(
        url=url, topic=topic,
        title="Seshat Test",
        message="If you see this, ntfy notifications are working!",
        tags=["white_check_mark", "seshat"],
    )
    return ValidateResponse(
        ok=ok,
        message="Test notification sent!" if ok else "Failed to send — check URL and topic",
    )
