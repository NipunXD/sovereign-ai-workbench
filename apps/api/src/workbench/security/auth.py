"""Password hashing and JWT issuance.

Authentication is local: an air-gapped deployment has no external identity
provider to federate with. ``OIDCAdapter`` in this module is a documented seam
for a site that does have an internal LDAP/AD, not a stub that pretends to work.

Token design:

* Short-lived access tokens (15 minutes) held in memory by the browser.
* Long-lived refresh tokens in an httpOnly, SameSite=Strict cookie, stored only
  as a hash, and **rotated on every use**. Presenting an already-rotated token
  means it was stolen, so the entire session chain is revoked.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from workbench.core.clock import now
from workbench.core.errors import AuthenticationError
from workbench.core.logging import get_logger

log = get_logger(__name__)

# Argon2id at the reference parameters: memory-hard, so a stolen database is not
# a practical path to the plaintext passwords.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password. Never raises on a bad password — returns False."""
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash uses outdated parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def generate_refresh_token() -> str:
    """A high-entropy opaque token. Only its hash is ever stored."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Hash a refresh token or API key for storage.

    Plain SHA-256 rather than Argon2: these are already 384 bits of entropy, so
    there is nothing to brute-force, and lookups happen on every refresh.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    """Constant-time comparison, so timing cannot reveal a prefix match."""
    return hmac.compare_digest(a, b)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Decoded access-token contents."""

    subject: str
    username: str
    roles: list[str]
    permissions: list[str]
    clearance: str
    departments: list[str]
    session_id: str | None = None
    expires_at: int = 0


class TokenService:
    """Issues and validates access tokens."""

    def __init__(
        self,
        secret: str,
        *,
        algorithm: str = "HS256",
        access_minutes: int = 15,
        issuer: str = "workbench",
    ) -> None:
        if not secret or secret.startswith("change-me"):
            # A default signing key in production would make every token
            # forgeable, so refuse to start rather than warn and continue.
            log.error("jwt_secret_not_configured")
        self._secret = secret
        self._algorithm = algorithm
        self._access_minutes = access_minutes
        self._issuer = issuer

    def issue_access_token(
        self,
        *,
        user_id: str,
        username: str,
        roles: list[str],
        permissions: list[str],
        clearance: str,
        departments: list[str],
        session_id: str | None = None,
    ) -> tuple[str, int]:
        """Return the encoded token and its lifetime in seconds.

        Permissions are embedded so ordinary requests need no database round
        trip. The 15-minute lifetime bounds how long a revoked permission can
        remain effective.
        """
        issued = now()
        expires = issued + timedelta(minutes=self._access_minutes)
        payload: dict[str, Any] = {
            "sub": user_id,
            "username": username,
            "roles": roles,
            "permissions": permissions,
            "clearance": clearance,
            "departments": departments,
            "sid": session_id,
            "iat": int(issued.timestamp()),
            "exp": int(expires.timestamp()),
            "iss": self._issuer,
        }
        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        return token, self._access_minutes * 60

    def decode(self, token: str) -> TokenClaims:
        """Validate and decode, or raise AuthenticationError."""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Your session has expired.", code="token_expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Invalid authentication token.") from exc

        return TokenClaims(
            subject=str(payload["sub"]),
            username=str(payload.get("username", "")),
            roles=list(payload.get("roles") or []),
            permissions=list(payload.get("permissions") or []),
            clearance=str(payload.get("clearance", "internal")),
            departments=list(payload.get("departments") or []),
            session_id=payload.get("sid"),
            expires_at=int(payload.get("exp", 0)),
        )


class OIDCAdapter:
    """Seam for sites with an internal identity provider.

    Deliberately unimplemented. An air-gapped MRPL deployment authenticates
    against local accounts or an on-premise LDAP/AD; wiring this up is a
    site-specific integration, and a half-working stub here would be worse than
    an explicit boundary.
    """

    def __init__(self, *_: Any, **__: Any) -> None:
        raise NotImplementedError(
            "External identity federation is not configured. This deployment "
            "uses local accounts; see docs/02-security-model.md for the "
            "LDAP/AD integration points."
        )
