"""
Authentication endpoints for Seshat.

Routes under /api/auth:
  - GET  /check  → report authenticated state + first-run flag
  - POST /setup  → create the initial admin (one-shot)
  - POST /login  → password-based login + session cookie issue
  - POST /logout → clear the session cookie

The setup endpoint only works if no admin row exists. After the first
admin is created, /api/auth/setup returns 403 forever. To reset
credentials, an operator must delete the row directly from
seshat_auth.db with shell access.

Sessions live in seshat_auth.db (separate file from seshat.db) so
auth has its own permissions and backup story.
"""
import logging
import time

from fastapi import APIRouter, Body, HTTPException, Request, Response

from app.auth_db import get_auth_db
from app.auth_passwords import hash_password, verify_password
from app.auth_sessions import (
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME_SECONDS,
    create_session_token,
    verify_session_token,
)


logger = logging.getLogger("seshat.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ─── Helpers ─────────────────────────────────────────────────


async def _get_admin_user() -> dict | None:
    """Return the admin user row as a dict, or None.

    The auth_users table is constrained to a single row by convention
    (the setup endpoint refuses to create a second one); LIMIT 1 is
    defensive.
    """
    db = await get_auth_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, password_hash, last_login_at, "
            "failed_login_count, failed_login_locked_until "
            "FROM auth_users LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


def _set_session_cookie(response: Response, request: Request, user_id: int) -> None:
    """Issue a signed session cookie tied to user_id.

    Cookie is marked Secure when the request scheme is HTTPS (or the
    proxy says so via X-Forwarded-Proto). Plain HTTP requests get a
    non-Secure cookie so local dev keeps working.
    """
    token = create_session_token(user_id)
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_LIFETIME_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


# ─── Routes ──────────────────────────────────────────────────


@router.get("/check")
async def auth_check(request: Request):
    """Report current auth state. Hit by the SPA on every page load.

    Response shapes:
      {"authenticated": True,  "username": "...", "first_run": False}
      {"authenticated": False, "first_run": True}     # no admin yet
      {"authenticated": False, "first_run": False}    # admin exists, no valid session
    """
    admin = await _get_admin_user()
    if not admin:
        return {"authenticated": False, "first_run": True}

    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    user_id = verify_session_token(token)
    if user_id == admin["id"]:
        return {
            "authenticated": True,
            "username": admin["username"],
            "first_run": False,
        }
    return {"authenticated": False, "first_run": False}


@router.post("/setup")
async def auth_setup(request: Request, response: Response, body: dict = Body(...)):
    """Create the initial admin account. ONLY works when no admin exists.

    Body: {"username": "...", "password": "..."}
    """
    existing = await _get_admin_user()
    if existing:
        raise HTTPException(403, "An admin account already exists")

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if len(username) < 3 or len(username) > 64:
        raise HTTPException(400, "Username must be 3-64 characters")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if len(password) > 256:
        raise HTTPException(400, "Password must be at most 256 characters")

    pwd_hash = hash_password(password)
    db = await get_auth_db()
    try:
        cursor = await db.execute(
            "INSERT INTO auth_users (username, password_hash, created_at) "
            "VALUES (?, ?, ?)",
            (username, pwd_hash, time.time()),
        )
        new_user_id = cursor.lastrowid
        await db.commit()
    finally:
        await db.close()

    logger.info(f"Admin account created: '{username}'")
    _set_session_cookie(response, request, new_user_id)
    return {"status": "ok", "username": username}


@router.post("/login")
async def auth_login(request: Request, response: Response, body: dict = Body(...)):
    """Authenticate and issue a session cookie.

    After 5 failed attempts the account is locked for 5 minutes.
    Successful login resets the failed-attempt counter.
    """
    admin = await _get_admin_user()
    if not admin:
        raise HTTPException(404, "No admin account exists — run setup first")

    locked_until = admin.get("failed_login_locked_until")
    if locked_until and locked_until > time.time():
        seconds_remaining = int(locked_until - time.time())
        raise HTTPException(
            429,
            f"Too many failed attempts. Try again in {seconds_remaining} seconds.",
        )

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if username != admin["username"] or not verify_password(password, admin["password_hash"]):
        new_count = (admin.get("failed_login_count") or 0) + 1
        new_locked_until = time.time() + 300 if new_count >= 5 else None

        db = await get_auth_db()
        try:
            await db.execute(
                "UPDATE auth_users SET failed_login_count=?, failed_login_locked_until=? WHERE id=?",
                (new_count, new_locked_until, admin["id"]),
            )
            await db.commit()
        finally:
            await db.close()

        logger.warning(
            f"Failed login attempt for '{admin['username']}' (count={new_count})"
        )
        raise HTTPException(401, "Invalid username or password")

    db = await get_auth_db()
    try:
        await db.execute(
            "UPDATE auth_users SET failed_login_count=0, "
            "failed_login_locked_until=NULL, last_login_at=? WHERE id=?",
            (time.time(), admin["id"]),
        )
        await db.commit()
    finally:
        await db.close()

    logger.info(f"Successful login: '{admin['username']}'")
    _set_session_cookie(response, request, admin["id"])
    return {"status": "ok", "username": admin["username"]}


@router.post("/logout")
async def auth_logout(response: Response):
    """Clear the session cookie. Always returns success."""
    _clear_session_cookie(response)
    return {"status": "ok"}
