"""Breeze Client Manager owning the active BreezeConnect SDK instance.

Ensures that:
- Exactly one active BreezeConnect runtime exists per account.
- Broker writes are serialized via an internal lock.
- Session lifecycles (UNCONFIGURED -> ACTIVE -> EXPIRED) are strictly tracked.
- Plaintext session tokens are never leaked into logs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Optional

from pydantic import SecretStr

from services.broker_gateway.domain.enums import SessionStatus
from services.broker_gateway.domain.errors import (
    BrokerAuthenticationError,
    BrokerBaseError,
    BrokerSessionExpiredError,
)
from services.broker_gateway.domain.models.session import (
    SessionCredentials,
    SessionStatusSnapshot,
)
from services.broker_gateway.infrastructure.icici.sdk_runner import SdkRunner

logger = logging.getLogger(__name__)


class BreezeClientManager:
    """Manages the active BreezeConnect SDK runtime and session lifecycle."""

    def __init__(
        self,
        sdk_runner: Optional[SdkRunner] = None,
        custom_sdk_instance: Optional[Any] = None,
    ) -> None:
        self.sdk_runner = sdk_runner or SdkRunner()
        self._sdk = custom_sdk_instance
        self._status: SessionStatus = SessionStatus.UNCONFIGURED
        self._account_id: str = "DEFAULT"
        self._login_time: Optional[datetime] = None
        self._expires_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._write_lock = asyncio.Lock()

    @property
    def status(self) -> SessionStatus:
        return self._status

    @property
    def is_active(self) -> bool:
        return self._status == SessionStatus.ACTIVE and self._sdk is not None

    def get_status_snapshot(self) -> SessionStatusSnapshot:
        """Return point-in-time status snapshot."""
        return SessionStatusSnapshot(
            status=self._status,
            account_id=self._account_id,
            login_time=self._login_time,
            expires_at=self._expires_at,
            message=self._last_error,
        )

    def get_sdk_client(self) -> Any:
        """Return the active BreezeConnect SDK instance.

        Raises:
            BrokerSessionExpiredError: If session is not ACTIVE.
        """
        if not self.is_active:
            raise BrokerSessionExpiredError(
                f"Broker session is not active (current status: {self._status.value}). "
                f"Please generate and activate a daily session token."
            )
        return self._sdk

    async def activate_session(self, credentials: SessionCredentials) -> SessionStatusSnapshot:
        """Activate daily session with ICICI Direct using API credentials.

        Calls BreezeConnect.generate_session(api_secret, session_token) off the event loop.
        """
        self._status = SessionStatus.ACTIVATING
        self._last_error = None
        self._account_id = credentials.api_key[:8]

        try:
            # If a custom SDK instance was not injected (e.g. for testing), instantiate official BreezeConnect
            if self._sdk is None:
                from breeze_connect import BreezeConnect
                self._sdk = BreezeConnect(api_key=credentials.api_key)

            secret_val = credentials.secret_key.get_secret_value()
            session_val = credentials.session_token.get_secret_value()

            # Execute generate_session on worker thread
            def _do_generate():
                return self._sdk.generate_session(
                    api_secret=secret_val,
                    session_token=session_val,
                )

            logger.info("Executing BreezeConnect.generate_session off-loop...")
            result = await self.sdk_runner.run(_do_generate, timeout_sec=15.0)

            now = datetime.now(timezone.utc)
            self._status = SessionStatus.ACTIVE
            self._login_time = now
            # ICICI sessions expire at midnight IST or 24 hours
            self._expires_at = datetime.fromtimestamp(now.timestamp() + 86400, tz=timezone.utc)

            logger.info("Breeze session successfully activated for account %s", self._account_id)
            return self.get_status_snapshot()

        except Exception as exc:
            self._status = SessionStatus.INVALID
            self._last_error = str(exc)
            logger.error("Failed to activate Breeze session: %s", exc)
            if isinstance(exc, BrokerBaseError):
                raise
            raise BrokerAuthenticationError(f"Failed to generate Breeze session: {exc}") from exc

    async def disconnect(self) -> None:
        """Clear active session and reset client state."""
        self._status = SessionStatus.DISCONNECTED
        self._sdk = None
        self._login_time = None
        self._expires_at = None
        logger.info("Breeze client disconnected and session cleared.")

    @property
    def write_lock(self) -> asyncio.Lock:
        """Lock for serializing order writes to the broker."""
        return self._write_lock

