"""Auth Service coordinating user authentication, JWT access token issuance, token rotation, and WS tickets.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Optional

import jwt

from libs.config.settings import PlatformSettings, get_platform_settings
from libs.contracts.models import (
    UserPrincipal,
    UserRole,
    generate_id,
    utc_now,
)
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.auth.repository import AuthRepository
from services.auth.security import (
    create_access_token,
    decode_access_token,
    generate_opaque_token,
    hash_token,
    verify_password,
)

logger = logging.getLogger(__name__)


class AuthService:
    """Core domain service for user authentication, token rotation, and RBAC principal resolution."""

    def __init__(
        self,
        repository: Optional[AuthRepository] = None,
        event_bus: Optional[EventBus] = None,
        settings: Optional[PlatformSettings] = None,
    ) -> None:
        self.settings = settings or get_platform_settings()
        self.repo = repository or AuthRepository(db_path=self.settings.auth_db_path)
        self.bus = event_bus or get_event_bus()

    async def initialize(self) -> None:
        await self.repo.initialize()

    async def authenticate(
        self,
        username: str,
        password: str,
        client_ip: str = "UNKNOWN",
    ) -> tuple[Optional[UserPrincipal], Optional[str], Optional[str], int]:
        """Authenticate user credentials.

        Returns:
            (user_principal, access_token, refresh_token, expires_in_seconds)
            or (None, None, None, 0) on failure.
        """
        user_row = await self.repo.get_user_by_username(username)
        if not user_row or not user_row.get("is_active"):
            await self._audit_auth_event(
                "USER_LOGIN_FAILURE",
                {"username": username, "reason": "User not found or inactive", "client_ip": client_ip},
            )
            return None, None, None, 0

        if not verify_password(password, user_row["password_hash"], user_row["salt"]):
            await self._audit_auth_event(
                "USER_LOGIN_FAILURE",
                {"username": username, "reason": "Invalid credentials", "client_ip": client_ip},
            )
            return None, None, None, 0

        # Create user principal
        principal = UserPrincipal(
            user_id=user_row["user_id"],
            username=user_row["username"],
            role=UserRole(user_row["role"]),
            is_active=bool(user_row["is_active"]),
        )

        # Generate JWT access token
        access_delta = timedelta(minutes=self.settings.access_token_expire_minutes)
        signing_key = self.settings.get_auth_signing_key()
        access_token = create_access_token(
            user_id=principal.user_id,
            username=principal.username,
            role=principal.role.value,
            signing_key=signing_key,
            expires_delta=access_delta,
        )

        # Generate opaque refresh token
        raw_refresh = generate_opaque_token(32)
        refresh_hash = hash_token(raw_refresh)
        refresh_expires = utc_now() + timedelta(days=self.settings.refresh_token_expire_days)

        await self.repo.store_refresh_token(
            token_id=generate_id(),
            user_id=principal.user_id,
            token_hash=refresh_hash,
            expires_at=refresh_expires,
        )

        await self._audit_auth_event(
            "USER_LOGIN_SUCCESS",
            {"user_id": principal.user_id, "username": principal.username, "role": principal.role.value, "client_ip": client_ip},
        )

        logger.info("User '%s' (%s) logged in successfully.", principal.username, principal.role.value)
        return principal, access_token, raw_refresh, int(access_delta.total_seconds())

    async def refresh_tokens(
        self,
        raw_refresh_token: str,
    ) -> tuple[Optional[UserPrincipal], Optional[str], Optional[str], int]:
        """Rotate refresh token: revoke old token and issue a fresh token pair."""
        token_hash = hash_token(raw_refresh_token)
        token_row = await self.repo.get_active_refresh_token(token_hash)
        if not token_row:
            return None, None, None, 0

        # Revoke old refresh token (rotation policy)
        await self.repo.revoke_refresh_token(token_hash)

        user_row = await self.repo.get_user_by_id(token_row["user_id"])
        if not user_row or not user_row.get("is_active"):
            return None, None, None, 0

        principal = UserPrincipal(
            user_id=user_row["user_id"],
            username=user_row["username"],
            role=UserRole(user_row["role"]),
            is_active=bool(user_row["is_active"]),
        )

        # Issue new access token
        access_delta = timedelta(minutes=self.settings.access_token_expire_minutes)
        signing_key = self.settings.get_auth_signing_key()
        new_access = create_access_token(
            user_id=principal.user_id,
            username=principal.username,
            role=principal.role.value,
            signing_key=signing_key,
            expires_delta=access_delta,
        )

        # Issue new refresh token
        new_raw_refresh = generate_opaque_token(32)
        new_refresh_hash = hash_token(new_raw_refresh)
        new_refresh_expires = utc_now() + timedelta(days=self.settings.refresh_token_expire_days)

        await self.repo.store_refresh_token(
            token_id=generate_id(),
            user_id=principal.user_id,
            token_hash=new_refresh_hash,
            expires_at=new_refresh_expires,
        )

        await self._audit_auth_event(
            "TOKEN_REFRESHED",
            {"user_id": principal.user_id, "username": principal.username},
        )
        return principal, new_access, new_raw_refresh, int(access_delta.total_seconds())

    async def logout(self, raw_refresh_token: str) -> bool:
        """Revoke active refresh token on logout."""
        token_hash = hash_token(raw_refresh_token)
        revoked = await self.repo.revoke_refresh_token(token_hash)
        if revoked:
            await self._audit_auth_event(
                "USER_LOGOUT",
                {"token_hash": token_hash[:8] + "..."},
            )
        return revoked

    async def create_ws_ticket(self, user: UserPrincipal) -> tuple[str, int]:
        """Generate a short-lived single-use ticket for WebSocket authentication."""
        raw_ticket = f"wst_{generate_opaque_token(24)}"
        ticket_hash = hash_token(raw_ticket)
        expire_sec = self.settings.ws_ticket_expire_seconds
        expires_at = utc_now() + timedelta(seconds=expire_sec)

        await self.repo.store_ws_ticket(
            ticket_id=generate_id(),
            ticket_hash=ticket_hash,
            user_id=user.user_id,
            role=user.role.value,
            expires_at=expires_at,
        )

        await self._audit_auth_event(
            "WS_TICKET_ISSUED",
            {"user_id": user.user_id, "role": user.role.value, "expires_in": expire_sec},
        )
        return raw_ticket, expire_sec

    async def validate_and_consume_ws_ticket(self, raw_ticket: str) -> Optional[UserPrincipal]:
        """Validate and claim single-use ticket for WebSocket handshake."""
        ticket_hash = hash_token(raw_ticket)
        ticket_row = await self.repo.consume_ws_ticket(ticket_hash)
        if not ticket_row:
            await self._audit_auth_event(
                "WS_AUTHENTICATION_FAILURE",
                {"reason": "Invalid, expired, or already consumed ticket"},
            )
            return None

        user_row = await self.repo.get_user_by_id(ticket_row["user_id"])
        if not user_row or not user_row.get("is_active"):
            return None

        principal = UserPrincipal(
            user_id=user_row["user_id"],
            username=user_row["username"],
            role=UserRole(user_row["role"]),
            is_active=bool(user_row["is_active"]),
        )

        await self._audit_auth_event(
            "WS_AUTHENTICATION_SUCCESS",
            {"user_id": principal.user_id, "username": principal.username, "role": principal.role.value},
        )
        return principal

    def verify_access_token(self, token: str) -> UserPrincipal:
        """Decode and validate access token signature and expiration.

        Raises:
            jwt.ExpiredSignatureError: Token expired.
            jwt.PyJWTError: Invalid token signature or format.
        """
        signing_key = self.settings.get_auth_signing_key()
        claims = decode_access_token(token, signing_key)
        return UserPrincipal(
            user_id=claims["sub"],
            username=claims["username"],
            role=UserRole(claims["role"]),
            is_active=True,
        )

    async def _audit_auth_event(self, event_type: str, details: dict[str, Any]) -> None:
        """Publish authentication audit events."""
        try:
            await self.bus.publish(
                EventEnvelope(
                    topic=Topics.AUDIT_EVENT,
                    payload={"event_type": event_type, "timestamp": utc_now().isoformat(), **details},
                )
            )
        except Exception as e:
            logger.warning("Failed to publish audit event %s: %s", event_type, e)

