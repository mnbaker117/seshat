"""
Authentication secret management for Seshat.

The auth secret signs session cookies (via itsdangerous). It comes from,
in priority order:

  1. The SESHAT_AUTH_SECRET environment variable (operator-friendly,
     supports Docker/k8s secrets, .env files, etc.). When set, no file
     is written.
  2. A persisted file at <data_dir>/auth_secret (the default for users
     who don't set the env var). 0600 on POSIX. Generated on first run.
  3. Generated fresh and held in memory only as a last resort if both
     above fail. Sessions then last only the lifetime of the process
     and a loud error is logged.

DO NOT change the secret after the first run — every existing session
becomes invalid and forces re-login. The secret is stable for the
lifetime of the deployment. If lost, the only side effect is "everyone
logs in again" — no data loss.
"""
import logging
import os
import secrets
from pathlib import Path

from app.runtime import get_data_dir

logger = logging.getLogger("seshat.auth")

_SECRET_FILENAME = "auth_secret"
_ENV_VAR_NAME = "SESHAT_AUTH_SECRET"
_MIN_LEN = 32

_cached_secret: str | None = None


def get_auth_secret() -> str:
    """Return the auth secret, generating + persisting it on first call.

    Cached after the first read. Generated values are 64-character
    URL-safe random strings (48 random bytes → ~64 base64 chars).
    Env-supplied secrets pass through unchanged provided they meet the
    minimum length.
    """
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret

    # Priority 1: env var override.
    env_secret = os.environ.get(_ENV_VAR_NAME, "").strip()
    if env_secret:
        if len(env_secret) < _MIN_LEN:
            logger.error(
                f"{_ENV_VAR_NAME} is set but shorter than {_MIN_LEN} chars — "
                "ignoring and falling back to file/generated secret."
            )
        else:
            logger.info(f"Using auth secret from {_ENV_VAR_NAME} env var")
            _cached_secret = env_secret
            return _cached_secret

    # Priority 2: persisted secret file.
    secret_path = Path(get_data_dir()) / _SECRET_FILENAME

    if secret_path.exists():
        try:
            existing = secret_path.read_text().strip()
            if len(existing) >= _MIN_LEN:
                _cached_secret = existing
                return _cached_secret
            logger.warning(
                f"Auth secret at {secret_path} is shorter than {_MIN_LEN} chars — regenerating"
            )
        except OSError as e:
            logger.warning(
                f"Could not read auth secret at {secret_path}: {e} — regenerating"
            )

    new_secret = secrets.token_urlsafe(48)
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_text(new_secret)
        try:
            os.chmod(secret_path, 0o600)
        except (OSError, NotImplementedError):
            pass
        logger.info(f"Generated new auth secret at {secret_path}")
    except OSError as e:
        logger.error(
            f"Failed to persist auth secret to {secret_path}: {e}. "
            "Sessions will be invalidated on every restart until this is fixed."
        )

    _cached_secret = new_secret
    return _cached_secret
