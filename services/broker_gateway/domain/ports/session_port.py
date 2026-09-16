"""Broker Session Port Protocol.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import SecretStr

from services.broker_gateway.domain.models.session import (
    SessionCredentials,
    SessionStatusSnapshot,
)


class BrokerSessionPort(Protocol):
    """Port for managing broker session activation, validation, and lifecycle."""

    async def activate(self, credentials: SessionCredentials) -> SessionStatusSnapshot:
        """Activate daily session using API key, secret, and session token."""
        ...

    async def validate(self) -> SessionStatusSnapshot:
        """Validate active session status with a low-risk query (e.g. get_funds)."""
        ...

    async def disconnect(self) -> None:
        """Disconnect and clear session."""
        ...

    async def get_status(self) -> SessionStatusSnapshot:
        """Get current session status snapshot."""
        ...

