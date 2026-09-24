"""Server-side LIVE trading activation gate and two-step verification engine.

Invariants:
- LIVE trading is disabled by default.
- Browser UI state alone can NEVER grant LIVE authority.
- Requires two-step confirmation challenge and account allowlisting.
- Time-bounded authorization window with auto-expiry.
- Instant emergency revocation capability.
- Full audit event logging on every activation attempt and state change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Optional
import uuid

from libs.config.settings import PlatformSettings, get_platform_settings
from libs.contracts.models import generate_id, utc_now
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus

logger = logging.getLogger(__name__)


class LiveTradingGate:
    """Manages server-side authorization for LIVE broker execution."""

    def __init__(
        self,
        settings: Optional[PlatformSettings] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.settings = settings or get_platform_settings()
        self.bus = event_bus or get_event_bus()

        self._configured_enabled: bool = self.settings.live_trading_enabled
        self._authorized: bool = False
        self._expires_at: Optional[datetime] = None
        self._allowed_accounts: set[str] = set(self.settings.live_allowed_accounts)
        self._pending_challenges: dict[str, dict[str, Any]] = {}

    def get_status(self) -> dict[str, Any]:
        """Return current server-side LIVE authorization status."""
        active = self.is_live_active()
        return {
            "live_authorized": active,
            "system_setting_enabled": self._configured_enabled,
            "expires_at": self._expires_at.isoformat() if self._expires_at else None,
            "allowed_account_count": len(self._allowed_accounts),
            "time_remaining_sec": max(
                0, int((self._expires_at - utc_now()).total_seconds())
            )
            if self._expires_at and active
            else 0,
        }

    def is_live_active(self) -> bool:
        """Check if LIVE mode is currently authorized and active window has not expired."""
        if not self._configured_enabled or not self._authorized:
            return False
        if self._expires_at is None or utc_now() >= self._expires_at:
            if self._expires_at and utc_now() >= self._expires_at:
                logger.warning("LIVE trading authorization window has expired.")
            return False
        return True

    def validate_live_order(self, account_id: str = "ICICI_PRIMARY") -> tuple[bool, str]:
        """Fail-closed validation before any live order intent or execution is processed."""
        if not self.is_live_active():
            return False, "LIVE trading is not authorized or active window has expired."

        if self._allowed_accounts and account_id not in self._allowed_accounts:
            return False, f"Account '{account_id}' is not in the approved LIVE allowlist."

        return True, "Authorized"

    async def request_activation_challenge(
        self,
        operator_id: str,
        account_id: str,
        duration_minutes: int = 30,
    ) -> dict[str, Any]:
        """Step 1 of Two-Step Confirmation: Request an activation challenge code."""
        if not self._configured_enabled:
            raise PermissionError(
                "LIVE_TRADING_ENABLED is false; server LIVE capability is disabled."
            )
        if account_id not in self._allowed_accounts:
            raise PermissionError(
                f"Account '{account_id}' is not in LIVE_ALLOWED_ACCOUNTS."
            )
        challenge_id = f"CHAL-{uuid.uuid4().hex[:8].upper()}"
        challenge_token = f"CONFIRM-{uuid.uuid4().hex[:6].upper()}"
        expires_at = utc_now() + timedelta(minutes=5)  # Challenge valid for 5 mins

        self._pending_challenges[challenge_id] = {
            "challenge_token": challenge_token,
            "operator_id": operator_id,
            "account_id": account_id,
            "duration_minutes": min(duration_minutes, 480),  # Max 8 hours
            "expires_at": expires_at,
        }

        # Audit challenge request
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.AUDIT_EVENT,
                payload={
                    "event_type": "LIVE_ACTIVATION_CHALLENGE_ISSUED",
                    "challenge_id": challenge_id,
                    "operator_id": operator_id,
                    "account_id": account_id,
                    "duration_minutes": duration_minutes,
                },
            )
        )

        logger.info(
            "Issued LIVE activation challenge %s for operator %s (account: %s)",
            challenge_id,
            operator_id,
            account_id,
        )

        return {
            "challenge_id": challenge_id,
            "challenge_token": challenge_token,
            "expires_in_seconds": 300,
            "message": "Two-step confirmation token generated. Submit token to activate LIVE window.",
        }

    async def confirm_activation(
        self,
        challenge_id: str,
        challenge_token: str,
        operator_id: str,
    ) -> bool:
        """Step 2 of Two-Step Confirmation: Submit token to open authorized LIVE window."""
        challenge = self._pending_challenges.get(challenge_id)
        if not challenge:
            logger.warning("Invalid challenge ID %s for LIVE activation", challenge_id)
            return False

        if utc_now() > challenge["expires_at"]:
            del self._pending_challenges[challenge_id]
            logger.warning("Expired challenge %s for LIVE activation", challenge_id)
            return False

        if challenge["challenge_token"] != challenge_token.strip():
            logger.warning("Incorrect challenge token for challenge %s", challenge_id)
            return False

        if challenge["operator_id"] != operator_id:
            logger.warning("Operator mismatch for challenge %s", challenge_id)
            return False

        if not self._configured_enabled:
            logger.warning("LIVE activation rejected because server capability is disabled")
            return False
        if challenge["account_id"] not in self._allowed_accounts:
            logger.warning("LIVE activation rejected because account is no longer allowlisted")
            return False

        # Activation confirmed within the immutable configured capability.
        duration = timedelta(minutes=challenge["duration_minutes"])
        self._authorized = True
        self._expires_at = utc_now() + duration
        del self._pending_challenges[challenge_id]

        # Audit event
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.AUDIT_EVENT,
                payload={
                    "event_type": "LIVE_MODE_ACTIVATED",
                    "operator_id": operator_id,
                    "account_id": challenge["account_id"],
                    "expires_at": self._expires_at.isoformat(),
                    "duration_minutes": challenge["duration_minutes"],
                },
            )
        )

        logger.warning(
            "LIVE TRADING HAS BEEN ACTIVATED by %s until %s (account: %s)",
            operator_id,
            self._expires_at.isoformat(),
            challenge["account_id"],
        )
        return True

    async def revoke_live_mode(
        self,
        operator_id: str = "OPERATOR",
        reason: str = "Operator manual revocation",
    ) -> None:
        """Emergency immediate revocation of LIVE authority."""
        self._authorized = False
        self._expires_at = None
        self._pending_challenges.clear()

        await self.bus.publish(
            EventEnvelope(
                topic=Topics.AUDIT_EVENT,
                payload={
                    "event_type": "LIVE_MODE_REVOKED",
                    "operator_id": operator_id,
                    "reason": reason,
                    "revoked_at": utc_now().isoformat(),
                },
            )
        )
        logger.warning("LIVE TRADING REVOKED immediately by %s: %s", operator_id, reason)

