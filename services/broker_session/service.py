"""Broker Session Service managing broker authentication lifecycle and health monitoring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import secrets
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
        # Compatibility fields mirror the configured LIVE execution broker.
        self._active_token: Optional[str] = None
        self._active_api_key: Optional[str] = None
        self._active_secret_key: Optional[str] = None
        self._active_credentials_by_broker: dict[str, dict[str, str]] = {}
        self._login_challenges: dict[str, dict[str, Any]] = {}

    def issue_login_challenge(
        self,
        *,
        initiated_by: str,
        broker_backend: Optional[str] = None,
        ttl_seconds: int = 600,
    ) -> dict[str, Any]:
        """Issue a short-lived, one-time correlation state for broker login."""
        now = utc_now()
        self._purge_login_challenges(now)
        state = secrets.token_urlsafe(32)
        expires_at = now + timedelta(seconds=max(60, int(ttl_seconds)))
        self._login_challenges[state] = {
            "initiated_by": initiated_by,
            "broker_backend": (
                str(broker_backend).lower()
                if broker_backend
                else None
            ),
            "expires_at": expires_at,
        }
        return {
            "state": state,
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": int((expires_at - now).total_seconds()),
        }

    def validate_login_challenge(
        self,
        state: str,
        broker_backend: Optional[str] = None,
    ) -> bool:
        """Check whether a broker-login correlation state is still valid."""
        now = utc_now()
        self._purge_login_challenges(now)
        challenge = self._login_challenges.get(str(state or ""))
        if not challenge or challenge["expires_at"] <= now:
            return False
        expected = challenge.get("broker_backend")
        return not expected or expected == self._normalize_broker(broker_backend)

    def consume_login_challenge(self, state: str) -> Optional[dict[str, Any]]:
        """Consume a valid broker-login correlation state exactly once."""
        now = utc_now()
        self._purge_login_challenges(now)
        challenge = self._login_challenges.pop(str(state or ""), None)
        if not challenge or challenge["expires_at"] <= now:
            return None
        return {
            "initiated_by": challenge["initiated_by"],
            "broker_backend": challenge.get("broker_backend"),
            "expires_at": challenge["expires_at"].isoformat(),
        }

    def _purge_login_challenges(self, now: datetime) -> None:
        expired = [
            state
            for state, challenge in self._login_challenges.items()
            if challenge["expires_at"] <= now
        ]
        for state in expired:
            self._login_challenges.pop(state, None)

    def set_broker_gateway(self, broker_gateway: Any) -> None:
        """Inject broker gateway to wire live adapter session activation."""
        self.broker_gateway = broker_gateway

    def _normalize_broker(self, broker_backend: Optional[str] = None) -> str:
        if broker_backend:
            broker = str(broker_backend).lower()
            if broker in {"breeze", "kite"}:
                return broker
        if self.broker_gateway:
            return str(
                getattr(
                    self.broker_gateway,
                    "execution_broker_name",
                    getattr(self.broker_gateway, "active_broker_name", "breeze"),
                )
                or "breeze"
            ).lower()
        return "breeze"

    @staticmethod
    def _account_for_broker(broker: str) -> str:
        return "ZERODHA_PRIMARY" if broker == "kite" else "ICICI_PRIMARY"

    def get_login_url(
        self,
        api_key: Optional[str] = None,
        broker_backend: Optional[str] = None,
    ) -> str:
        """Return the requested broker's daily login URL."""
        broker = self._normalize_broker(broker_backend)
        key = api_key
        if not key:
            credentials = self._active_credentials_by_broker.get(broker, {})
            key = credentials.get("api_key", "")
        if not key:
            try:
                from libs.config import get_platform_settings
                cfg = get_platform_settings()
                key_secret = (
                    cfg.kite_api_key
                    if broker == "kite"
                    else cfg.breeze_api_key
                )
                key = key_secret.get_secret_value() if key_secret else ""
            except Exception:
                key = ""
        if broker == "kite":
            return f"https://kite.zerodha.com/connect/login?v=3&api_key={key or ''}"
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
        broker_backend: Optional[str] = None,
    ) -> dict[str, Any]:
        """Activate one broker session without replacing the other broker."""
        session_id = generate_id()
        now = utc_now()
        expires_at = now + timedelta(hours=expiry_hours)

        broker = self._normalize_broker(
            broker_backend
            or (
                "kite"
                if str(account_id).upper().startswith("ZERODHA")
                else "breeze"
            )
        )
        account_id = account_id or self._account_for_broker(broker)

        # Retain raw credentials in memory only, independently per broker.
        runtime_token = access_token or session_token
        self._active_credentials_by_broker[broker] = {
            "api_key": api_key,
            "secret_key": secret_key,
            "session_token": runtime_token,
        }
        execution_broker = self._normalize_broker()
        if broker == execution_broker:
            self._active_api_key = api_key
            self._active_secret_key = secret_key
            self._active_token = runtime_token

        # Mask token for persistence
        token_for_mask = access_token or session_token
        masked = token_for_mask[:4] + "..." + token_for_mask[-4:] if len(token_for_mask) > 8 else "***"

        await self.repo.save_session(
            session_id=session_id,
            account_id=account_id,
            session_token_masked=masked,
            login_time=now,
            expires_at=expires_at,
            metadata={
                "source": "USER_LOGIN",
                "broker": broker,
            },
            broker_name="ZERODHA" if broker == "kite" else "ICICI_DIRECT",
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

        # Propagate credentials only to the requested broker adapter.
        gateway_synced = False
        auth_error: Optional[str] = None
        if self.broker_gateway and hasattr(self.broker_gateway, "adapter_for_broker"):
            try:
                adapter = self.broker_gateway.adapter_for_broker(broker)
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
                logger.info(
                    "%s broker adapter authentication result: %s",
                    broker,
                    gateway_synced,
                )
            except Exception as exc:
                auth_error = str(exc)
                logger.warning("%s broker adapter authentication error: %s", broker, exc)

        if (
            not gateway_synced
            and self.broker_gateway
            and hasattr(self.broker_gateway, "adapter_for_broker")
        ):
            await self.repo.record_health_check("DISCONNECTED", latency_ms=0.0, message="Authentication failed")
            return {
                "session_id": session_id,
                "status": "AUTHENTICATION_FAILED",
                "connected": False,
                "account_id": account_id,
                "expires_at": expires_at.isoformat(),
                "gateway_synced": False,
                "broker": broker,
                "message": (
                    f"Authentication rejected by {broker.title()}: "
                    f"{auth_error or 'Session key is expired or invalid.'}"
                ),
            }

        logger.info("Broker session activated successfully: session_id=%s", session_id)
        return {
            "session_id": session_id,
            "status": "CONNECTED",
            "connected": True,
            "account_id": account_id,
            "expires_at": expires_at.isoformat(),
            "gateway_synced": gateway_synced,
            "broker": broker,
        }

    async def get_session_status(
        self,
        broker_backend: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return status for one broker; default is the LIVE execution broker."""
        broker = self._normalize_broker(broker_backend)
        account_id = self._account_for_broker(broker)
        credentials = self._active_credentials_by_broker.get(broker)
        active = await self.repo.get_active_session(account_id=account_id)
        if not active or not credentials:
            return {
                "status": "DISCONNECTED",
                "connected": False,
                "expires_at": None,
                "message": "No active broker session",
            }

        expires_at = datetime.fromisoformat(active["expires_at"])
        if utc_now() >= expires_at:
            await self.repo.expire_session(active["session_id"])
            self._active_credentials_by_broker.pop(broker, None)
            if broker == self._normalize_broker():
                self._active_token = None
            return {
                "status": "EXPIRED",
                "connected": False,
                "expires_at": active["expires_at"],
                "message": "Broker session has expired",
            }

        # Check whether this broker adapter is genuinely active.
        if self.broker_gateway and hasattr(self.broker_gateway, "adapter_for_broker"):
            adapter = self.broker_gateway.adapter_for_broker(broker)
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
                    "broker": broker,
                    "message": (
                        f"The {broker.title()} session is no longer active. "
                        "Please reconnect with today's token."
                    ),
                }

        return {
            "status": "CONNECTED",
            "connected": True,
            "session_id": active["session_id"],
            "account_id": active["account_id"],
            "expires_at": active["expires_at"],
            "token_masked": active["session_token_masked"],
            "broker": broker,
        }

    async def get_all_session_statuses(self) -> dict[str, dict[str, Any]]:
        return {
            "breeze": await self.get_session_status("breeze"),
            "kite": await self.get_session_status("kite"),
        }

    def get_runtime_credentials(
        self,
        broker_backend: Optional[str] = None,
    ) -> Optional[dict[str, str]]:
        """Return in-memory credentials for the requested broker."""
        broker = self._normalize_broker(broker_backend)
        credentials = self._active_credentials_by_broker.get(broker)
        if not credentials:
            return None
        return dict(credentials)
