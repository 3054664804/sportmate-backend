"""Password hashing and opaque bearer-token helpers.

Only the standard library is used, so the backend has no extra runtime
dependency beyond FastAPI, SQLAlchemy and Pydantic.

Password storage: PBKDF2-HMAC-SHA256 with a per-user random salt.
Token storage:    a 32-byte random token is returned to the client once, and
                  only its SHA-256 digest is persisted.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 240_000
SALT_BYTES = 16
TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    """Return ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM, password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification against a stored hash."""
    try:
        algorithm, iterations, salt_hex, hash_hex = stored.split("$")
        if not algorithm.startswith("pbkdf2_"):
            return False
        expected = bytes.fromhex(hash_hex)
        digest = hashlib.pbkdf2_hmac(
            algorithm.removeprefix("pbkdf2_"),
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest, expected)


def new_token() -> str:
    """Generate a URL-safe opaque token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_digest(token: str) -> str:
    """SHA-256 digest used for storage and lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_checkin_code(length: int) -> str:
    """Numeric check-in code handed out by the organiser."""
    return "".join(secrets.choice("0123456789") for _ in range(length))
