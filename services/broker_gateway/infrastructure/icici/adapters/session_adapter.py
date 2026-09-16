"""Breeze Session Adapter implementing BrokerSessionPort.
"""

from __future__ import annotations

import logging
from typing import Optional

from services.broker_gateway.domain.enums import SessionStatus
from services.broker_gateway.domain.errors import (
    BrokerAuthenticationError,
    BrokerBaseError,
)
from services.broker_gateway.domain.models.session import (
    SessionCredentials,
    SessionStatusSnapshot,
)
from services.broker_gateway.domain.ports.account_port import BrokerAccountPort
from services.broker_gateway.domain.ports.session_port import BrokerSessionPort
from services.broker_gateway.infrastructure.icici.breeze_client import BreezeClientManager

logger = logging.getLogger(__name__)


class BreezeSessionAdapter(BrokerSessionPort):
    """Manages ICICI Breeze session activation, low-risk validation, and disconnection."""

    def __init__(
        self,
        client_manager: BreezeClientManager,
        account_adapter: Optional[BrokerAccountPort] = None,
    ) -> None:
        self.client_manager = client_manager
        self.account_adapter = account_adapter

    def set_account_adapter(self, account_adapter: BrokerAccountPort) -> None:
        """Inject account adapter for low-risk session validation if initialized lazily."""
        self.account_adapter = account_adapter

    async def activate(self, credentials: SessionCredentials) -> SessionStatusSnapshot:
        """Activate daily session and execute low-risk validation (get_funds)."""
        snapshot = await self.client_manager.activate_session(credentials)

        # Low-risk post-activation validation (e.g. get_funds)
        if self.account_adapter is not None:
            try:
                logger.info("Executing post-activation session validation query (get_funds)...")
                await self.account_adapter.get_funds()
                logger.info("Breeze session validation succeeded.")
            except Exception as exc:
                logger.error("Breeze post-activation session validation failed: %s", exc)
                await self.client_manager.disconnect()
                if isinstance(exc, BrokerBaseError):
                    raise
                raise BrokerAuthenticationError(
                    f"Session activation succeeded but low-risk validation query failed: {exc}"
                ) from exc

        return self.client_manager.get_status_snapshot()

    async def validate(self) -> SessionStatusSnapshot:
        """Validate active session status using get_funds query."""
        if not self.client_manager.is_active:
            return self.client_manager.get_status_snapshot()

        if self.account_adapter is not None:
            try:
                await self.account_adapter.get_funds()
            except Exception as exc:
                logger.warning("Session validation check failed: %s", exc)
                return SessionStatusSnapshot(
                    status=SessionStatus.EXPIRED,
                    account_id=self.client_manager.get_status_snapshot().account_id,
                    login_time=self.client_manager.get_status_snapshot().login_time,
                    expires_at=self.client_manager.get_status_snapshot().expires_at,
                    message=f"Session validation failed: {exc}",
                )

        return self.client_manager.get_status_snapshot()

    async def disconnect(self) -> None:
        """Disconnect active session."""
        await self.client_manager.disconnect()

    async def get_status(self) -> SessionStatusSnapshot:
        """Return current session snapshot."""
        return self.client_manager.get_status_snapshot()

