"""
HTTP routers exposed by the FastAPI app.

Phase 1 ships only the manual-inject endpoint. Phase 3 will add the
review queue, author management, snatch budget dashboard, cookie
status, and audit log routers — all under `/api/v1/...`.

Routers don't reach into module globals: they read the dispatcher
out of `app.state` (set by main.py's lifespan) and dispatch through
that. This keeps tests free of monkey-patching: a test fixture
constructs a dispatcher with fakes, sets it on app.state, and then
calls the endpoint via httpx.AsyncClient.
"""
