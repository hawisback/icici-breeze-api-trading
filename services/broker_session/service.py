"""Broker Session Service managing broker authentication lifecycle and health monitoring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Optional

from libs.contracts.models import generate_id, utc_now
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.broker_session.repository import BrokerSessionRepository

logger = logging.getLogger(__name__)


class BrokerSessionService:
    """Coordinates broker session activation, validation, and status checks."""

    def __init__(
        self,
        repository: Optional[BrokerSessionRepository] = None,
        event_bus: Optional[EventBus] = None,
        broker_gateway: Optional[Any] = None,
    ) -> None:
        self.repo = repository or BrokerSessionRepository()
        self.bus = event_bus or get_event_bus()
        self.broker_gateway = broker_gateway
        self._active_token: Optional[str] = None
        self._active_api_key: Optional[str] = None
        self._active_secret_key: Optional[str] = None

    def set_broker_gateway(self, broker_gateway: Any) -> None:
        """Inject broker gateway to wire live adapter session activation."""
        self.broker_gateway = broker_gateway

    def get_login_url(self, api_key: Optional[str] = None) -> str:
        """Return the selected broker's daily login URL."""
        key = api_key or self._active_api_key
        if not key:
            try:
                from libs.config import get_platform_settings
                cfg = get_platform_settings()
                key_secret = cfg.kite_api_key if cfg.broker_backend.value == "kite" else cfg.breeze_api_key
                key = key_secret.get_secret_value() if key_secret else ""
            except Exception:
                key = ""
        try:
            from libs.config import get_platform_settings
            if get_platform_settings().broker_backend.value == "kite":
                return f"https://kite.zerodha.com/connect/login?v=3&api_key={key or ''}"
        except Exception:
            pass
        return f"https://api.icicidirect.com/apiuser/login?api_key={key or ''}"

    async def initialize(self) -> None:
        await self.repo.initialize()

    async def activate_session(
        self,
        api_key: str,
        secret_key: str,
        session_token: str,
        account_id: str = "ICICI_PRIMARY",
        expiry_hours: int = 24,
        access_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """Activate daily broker session."""
        session_id = generate_id()
        now = utc_now()
        expires_at = now + timedelta(hours=expiry_hours)

        # Retain raw credentials in memory only
        self._active_api_key = api_key
        self._active_secret_key = secret_key
        self._active_token = access_token or session_token

        # Mask token for persistence
        token_for_mask = access_token or session_token
        masked = token_for_mask[:4] + "..." + token_for_mask[-4:] if len(token_for_mask) > 8 else "***"

        await self.repo.save_session(
            session_id=session_id,
            account_id=account_id,
            session_token_masked=masked,
            login_time=now,
            expires_at=expires_at,
            metadata={"source": "USER_LOGIN"},
        )

        # Record health check
        await self.repo.record_health_check("CONNECTED", latency_ms=12.5, message="Session activated")

        # Emit audit event
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.AUDIT_EVENT,
                payload={
                    "event_type": "SESSION_ACTIVATED",
                    "session_id": session_id,
                    "account_id": account_id,
                    "expires_at": expires_at.isoformat(),
                },
            )
        )

        # Propagate credentials to live broker adapter if gateway is wired
        gateway_synced = False
        auth_error: Optional[str] = None
        if self.broker_gateway and hasattr(self.broker_gateway, "active_adapter"):
            try:
                adapter = self.broker_gateway.active_adapter
                if access_token and hasattr(adapter, "authenticate_access_token"):
                    gateway_synced = await adapter.authenticate_access_token(
                        api_key=api_key,
                        access_token=access_token,
                    )
                else:
                    gateway_synced = await adapter.authenticate(
                        api_key=api_key,
                        secret_key=secret_key,
                        session_token=session_token,
                    )
                logger.info("%s live adapter authentication result: %s", self.broker_gateway.active_broker_name, gateway_synced)
            except Exception as exc:
                auth_error = str(exc)
                logger.warning("Breeze live adapter authentication error: %s", exc)

        if not gateway_synced and self.broker_gateway and hasattr(self.broker_gateway, "active_adapter"):
            await self.repo.record_health_check("DISCONNECTED", latency_ms=0.0, message="Authentication failed")
            return {
                "session_id": session_id,
                "status": "AUTHENTICATION_FAILED",
                "connected": False,
                "account_id": account_id,
                "expires_at": expires_at.isoformat(),
                "gateway_synced": False,
                "message": f"Authentication rejected by ICICI Direct: {auth_error or 'Session key is expired or invalid.'}",
            }

        logger.info("Broker session activated successfully: session_id=%s", session_id)
        return {
            "session_id": session_id,
            "status": "CONNECTED",
            "connected": True,
            "account_id": account_id,
            "expires_at": expires_at.isoformat(),
            "gateway_synced": gateway_synced,
        }

    async def get_session_status(self) -> dict[str, Any]:
        """Return active session status and expiry countdown."""
        active = await self.repo.get_active_session()
        if not active or not self._active_token:
            return {
                "status": "DISCONNECTED",
                "connected": False,
                "expires_at": None,
                "message": "No active broker session",
            }

        expires_at = datetime.fromisoformat(active["expires_at"])
        if utc_now() >= expires_at:
            await self.repo.expire_session(active["session_id"])
            self._active_token = None
            return {
                "status": "EXPIRED",
                "connected": False,
                "expires_at": active["expires_at"],
                "message": "Broker session has expired",
            }

        # Check if the selected live adapter is genuinely active.
        if self.broker_gateway and hasattr(self.broker_gateway, "active_adapter"):
            adapter = self.broker_gateway.active_adapter
            is_active = getattr(adapter, "is_active", False)
            if not is_active and hasattr(adapter, "client_manager"):
                is_active = getattr(adapter.client_manager, "is_active", False)
            if not is_active:
                return {
                    "status": "EXPIRED",
                    "connected": False,
                    "session_id": active["session_id"],
                    "account_id": active["account_id"],
                    "expires_at": active["expires_at"],
                    "token_masked": active["session_token_masked"],
                    "message": "The selected broker session is no longer active. Please reconnect with today's token.",
                }

        return {
            "status": "CONNECTED",
            "connected": True,
            "session_id": active["session_id"],
            "account_id": active["account_id"],
            "expires_at": active["expires_at"],
            "token_masked": active["session_token_masked"],
        }

    def get_runtime_credentials(self) -> Optional[dict[str, str]]:
        """Return in-memory active credentials for broker requests."""
        if not self._active_token:
            return None
        return {
            "api_key": self._active_api_key or "",
            "secret_key": self._active_secret_key or "",
            "session_token": self._active_token or "",
        }
