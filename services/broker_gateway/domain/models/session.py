"""Domain Session Models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from pydantic import SecretStr

from services.broker_gateway.domain.enums import SessionStatus


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    """Masked broker session credentials."""

    api_key: str
    secret_key: SecretStr
    session_token: SecretStr


@dataclass(frozen=True, slots=True)
class SessionStatusSnapshot:
    """Point-in-time snapshot of broker session health and expiry."""

    status: SessionStatus
    account_id: str
    login_time: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    message: Optional[str] = None

