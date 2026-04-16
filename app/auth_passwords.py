"""
Password hashing and verification using bcrypt directly.

We call bcrypt directly rather than going through passlib to avoid the
long-standing passlib 1.7.4 ↔ bcrypt 4.x layering bug. The API is small
enough that the extra dependency layer is not worth its weight.

Work factor: 12 rounds — modern default, slow enough to be expensive
to brute-force, fast enough to verify in <300ms on Unraid/NUC hardware.

Bcrypt has a hard 72-byte input limit. We pre-truncate so behavior is
consistent across implementations; the API layer separately enforces a
256-character upper bound.
"""
import bcrypt

_BCRYPT_ROUNDS = 12
_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(plain_password: str) -> bytes:
    return plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain_password: str) -> str:
    """Hash a plain password using bcrypt. Salt is embedded in the
    returned string."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(_to_bcrypt_bytes(plain_password), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a stored bcrypt hash.

    Returns True on match, False otherwise. Any exception (corrupted
    hash, wrong algorithm prefix, etc.) is caught and treated as a
    failed verification.
    """
    try:
        return bcrypt.checkpw(
            _to_bcrypt_bytes(plain_password),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False
