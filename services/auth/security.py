"""Security primitives: PBKDF2 password hashing, token hashing, and HS256 JWT utilities.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any, Optional

import jwt

from libs.contracts.models import utc_now

PBKDF2_ITERATIONS = 100_000


def hash_password(password: str) -> tuple[str, str]:
    """Generate a secure PBKDF2-HMAC-SHA256 hash with a cryptographically random salt.

    Returns:
        (password_hash_hex, salt_hex)
    """
    salt_bytes = secrets.token_bytes(16)
    hash_bytes = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        PBKDF2_ITERATIONS,
    )
    return hash_bytes.hex(), salt_bytes.hex()


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Verify password against stored PBKDF2-HMAC-SHA256 hash using constant-time comparison."""
    try:
        salt_bytes = bytes.fromhex(salt)
        computed_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt_bytes,
            PBKDF2_ITERATIONS,
        ).hex()
        return secrets.compare_digest(computed_hash, password_hash)
    except Exception:
        return False


def hash_token(token: str) -> str:
    """Compute SHA-256 hex digest for opaque refresh tokens before persistence."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_opaque_token(nbytes: int = 32) -> str:
    """Generate a URL-safe cryptographically secure random token."""
    return secrets.token_urlsafe(nbytes)


def create_access_token(
    user_id: str,
    username: str,
    role: str,
    signing_key: str,
    expires_delta: timedelta,
) -> str:
    """Issue a signed HS256 JWT access token."""
    now = utc_now()
    payload: dict[str, Any] = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, signing_key, algorithm="HS256")


def decode_access_token(token: str, signing_key: str) -> dict[str, Any]:
    """Decode and validate a signed HS256 JWT access token.

    Raises:
        jwt.ExpiredSignatureError: If token has expired.
        jwt.PyJWTError: If signature or format is invalid.
    """
    return jwt.decode(token, signing_key, algorithms=["HS256"])

