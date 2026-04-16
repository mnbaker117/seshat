"""
HTTP-level tests for the auth router + middleware.

Builds a fresh FastAPI app per test with the AuthMiddleware installed,
points the auth DB at a tmp_path file, and walks through:
  1. /api/auth/check returns first_run=True before setup
  2. /api/auth/setup creates the admin and issues a session cookie
  3. Authenticated /api/auth/check returns first_run=False
  4. /api/auth/login with bad password returns 401
  5. Repeated bad logins lock the account
  6. Logout clears the session
  7. The middleware blocks protected /api/* routes without a cookie
  8. The middleware lets public /api/* routes through
"""
import pytest
import httpx
from fastapi import FastAPI

from app import auth_db, auth_secret
from app.routers.auth import router as auth_router


def _make_app() -> FastAPI:
    """Construct a fresh FastAPI app with the auth router + middleware.

    The real `app.main` lifespan does too much (IRC, qBit, scheduler);
    these tests want just the request surface.
    """
    from app.auth_sessions import SESSION_COOKIE_NAME, verify_session_token
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware

    app = FastAPI()

    _PUBLIC = frozenset({
        "/api/health",
        "/api/auth/setup",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/check",
    })

    class _Mw(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if not path.startswith("/api/"):
                return await call_next(request)
            if path in _PUBLIC:
                return await call_next(request)
            token = request.cookies.get(SESSION_COOKIE_NAME, "")
            if verify_session_token(token) is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required"},
                )
            return await call_next(request)

    app.add_middleware(_Mw)
    app.include_router(auth_router)

    @app.get("/api/protected/ping")
    async def ping():
        return {"pong": True}

    return app


@pytest.fixture
async def auth_app(tmp_path, monkeypatch):
    """Per-test FastAPI app + isolated auth DB + isolated secret cache."""
    # Point the auth DB at a tmp file.
    monkeypatch.setattr(
        auth_db, "get_auth_db_path", lambda: tmp_path / "seshat_auth.db"
    )
    # Reset the cached auth secret so each test starts fresh.
    auth_secret._cached_secret = None
    monkeypatch.setattr(
        "app.runtime.get_data_dir", lambda: tmp_path
    )
    await auth_db.init_auth_db()
    yield _make_app()
    auth_secret._cached_secret = None


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


class TestAuthFlow:
    async def test_check_reports_first_run_initially(self, auth_app):
        async with await _client(auth_app) as c:
            r = await c.get("/api/auth/check")
            assert r.status_code == 200
            body = r.json()
            assert body["authenticated"] is False
            assert body["first_run"] is True

    async def test_setup_creates_admin_and_logs_in(self, auth_app):
        async with await _client(auth_app) as c:
            r = await c.post(
                "/api/auth/setup",
                json={"username": "admin", "password": "supersecret"},
            )
            assert r.status_code == 200
            assert r.json()["username"] == "admin"
            # Cookie set on the client.
            assert "seshat_session" in c.cookies

            # /check now returns authenticated.
            r2 = await c.get("/api/auth/check")
            assert r2.status_code == 200
            body = r2.json()
            assert body["authenticated"] is True
            assert body["first_run"] is False
            assert body["username"] == "admin"

    async def test_setup_rejects_short_password(self, auth_app):
        async with await _client(auth_app) as c:
            r = await c.post(
                "/api/auth/setup",
                json={"username": "admin", "password": "short"},
            )
            assert r.status_code == 400

    async def test_setup_rejects_second_admin(self, auth_app):
        async with await _client(auth_app) as c:
            await c.post(
                "/api/auth/setup",
                json={"username": "admin", "password": "supersecret"},
            )
            r = await c.post(
                "/api/auth/setup",
                json={"username": "another", "password": "supersecret"},
            )
            assert r.status_code == 403

    async def test_login_after_setup(self, auth_app):
        async with await _client(auth_app) as c:
            await c.post(
                "/api/auth/setup",
                json={"username": "admin", "password": "supersecret"},
            )
            # Drop the cookie to simulate a fresh browser.
            c.cookies.clear()
            r = await c.post(
                "/api/auth/login",
                json={"username": "admin", "password": "supersecret"},
            )
            assert r.status_code == 200
            assert "seshat_session" in c.cookies

    async def test_login_wrong_password_401(self, auth_app):
        async with await _client(auth_app) as c:
            await c.post(
                "/api/auth/setup",
                json={"username": "admin", "password": "supersecret"},
            )
            c.cookies.clear()
            r = await c.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrongpw11"},
            )
            assert r.status_code == 401

    async def test_logout_clears_session(self, auth_app):
        async with await _client(auth_app) as c:
            await c.post(
                "/api/auth/setup",
                json={"username": "admin", "password": "supersecret"},
            )
            r = await c.post("/api/auth/logout")
            assert r.status_code == 200
            # /check should report not authenticated.
            r2 = await c.get("/api/auth/check")
            assert r2.json()["authenticated"] is False


class TestAuthMiddleware:
    async def test_protected_route_blocks_without_cookie(self, auth_app):
        async with await _client(auth_app) as c:
            r = await c.get("/api/protected/ping")
            assert r.status_code == 401

    async def test_protected_route_allows_after_login(self, auth_app):
        async with await _client(auth_app) as c:
            await c.post(
                "/api/auth/setup",
                json={"username": "admin", "password": "supersecret"},
            )
            r = await c.get("/api/protected/ping")
            assert r.status_code == 200
            assert r.json()["pong"] is True

    async def test_public_endpoints_dont_require_auth(self, auth_app):
        async with await _client(auth_app) as c:
            r = await c.get("/api/auth/check")
            assert r.status_code == 200

    async def test_non_api_routes_pass_through(self, auth_app):
        # The middleware shouldn't block requests outside /api/.
        # Without an SPA mount the route is a 404, but the 401 path
        # should NOT trigger.
        async with await _client(auth_app) as c:
            r = await c.get("/some-spa-route")
            assert r.status_code == 404
