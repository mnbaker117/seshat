"""
Session cookie creation and validation.

Sessions are signed cookies (NOT JWTs). The payload is a small dict
{user_id, issued_at} signed via itsdangerous'
URLSafeTimedSerializer. The signature is verified on every protected
API request by the auth middleware.

Why signed cookies instead of JWTs:
  - simpler to implement and reason about for a single-user app
  - none of JWT's standard footguns (alg=none, key confusion, claim
    parsing edge cases)
  - server-side invalidation is possible if we ever want it

Cookie security flags applied at issue time:
  - HttpOnly: cannot be read from JavaScript
  - SameSite=Lax: prevents CSRF from third-party sites
  - Secure: only set when the request was over HTTPS
  - Max-Age: 30 days
"""
import time
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.auth_secret import get_auth_secret


SESSION_COOKIE_NAME = "seshat_session"
SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_auth_secret(),
        salt="seshat-session",
    )


def create_session_token(user_id: int) -> str:
    """Create a signed session token for the given user ID."""
    serializer = _get_serializer()
    return serializer.dumps({"user_id": user_id, "issued_at": time.time()})


def verify_session_token(token: str) -> Optional[int]:
    """Return the user_id if the token is valid, else None.

    None covers: empty token, malformed payload, signature mismatch,
    expired token, anything weird. Callers treat None as "not
    authenticated, render login".
    """
    if not token:
        return None
    serializer = _get_serializer()
    try:
        payload = serializer.loads(token, max_age=SESSION_LIFETIME_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    except Exception:
        return None
    user_id = payload.get("user_id") if isinstance(payload, dict) else None
    if isinstance(user_id, int):
        return user_id
    return None
