"""Broker Session Service managing concurrent Breeze and Kite sessions."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Optional

from libs.config.settings import BrokerBackend, get_platform_settings
from libs.contracts.models import generate_id, utc_now
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.broker_session.repository import BrokerSessionRepository

logger = logging.getLogger(__name__)


class BrokerSessionService:
    """Coordinates independent broker sessions while preserving a default execution broker."""

    def __init__(
        self,
        repository: Optional[BrokerSessionRepository] = None,
        event_bus: Optional[EventBus] = None,
        broker_gateway: Optional[Any] = None,
    ) -> None:
        self.repo = repository or BrokerSessionRepository()
        self.bus = event_bus or get_event_bus()
        self.broker_gateway = broker_gateway
        self._runtime_credentials: dict[str, dict[str, str]] = {}

    def set_broker_gateway(self, broker_gateway: Any) -> None:
        self.broker_gateway = broker_gateway

    @staticmethod
    def _normalize_broker(
        broker: BrokerBackend | str | None,
        *,
        account_id: Optional[str] = None,
    ) -> BrokerBackend:
        if isinstance(broker, BrokerBackend):
            return broker
        if broker:
            return BrokerBackend(str(broker).lower())
        account = str(account_id or "").upper()
        if "ZERODHA" in account or "KITE" in account:
            return BrokerBackend.KITE
        if "ICICI" in account or "BREEZE" in account:
            return BrokerBackend.BREEZE
        return get_platform_settings().broker_backend

    def get_login_url(
        self,
        api_key: Optional[str] = None,
        broker: BrokerBackend | str | None = None,
    ) -> str:
        target = self._normalize_broker(broker)
        cfg = get_platform_settings()
        if api_key:
            key = api_key
        elif target == BrokerBackend.KITE:
            key = cfg.kite_api_key.get_secret_value() if cfg.kite_api_key else ""
        else:
            key = cfg.breeze_api_key.get_secret_value() if cfg.breeze_api_key else ""
        if target == BrokerBackend.KITE:
            return f"https://kite.zerodha.com/connect/login?v=3&api_key={key}"
        return f"https://api.icicidirect.com/apiuser/login?api_key={key}"

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
        broker: BrokerBackend | str | None = None,
    ) -> dict[str, Any]:
        target = self._normalize_broker(broker, account_id=account_id)
        session_id = generate_id()
        now = utc_now()
        expires_at = now + timedelta(hours=expiry_hours)
        token_value = access_token or session_token
        masked = (
            token_value[:4] + "..." + token_value[-4:]
            if len(token_value) > 8
            else "***"
        )

        await self.repo.save_session(
            session_id=session_id,
            account_id=account_id,
            session_token_masked=masked,
            login_time=now,
            expires_at=expires_at,
            metadata={"source": "USER_LOGIN", "broker": target.value},
        )

        gateway_synced = False
        auth_error: Optional[str] = None
        if self.broker_gateway is not None:
            try:
                gateway_synced = await self.broker_gateway.authenticate_broker(
                    target,
                    api_key=api_key,
                    secret_key=secret_key,
                    session_token=session_token,
                    access_token=access_token,
                )
            except Exception as exc:
                auth_error = str(exc)
                logger.warning("%s live adapter authentication error: %s", target.value, exc)

        if not gateway_synced and self.broker_gateway is not None:
            await self.repo.record_health_check(
                "DISCONNECTED",
                latency_ms=0.0,
                message=f"{target.value} authentication failed",
            )
            return {
                "session_id": session_id,
                "status": "AUTHENTICATION_FAILED",
                "connected": False,
                "broker": target.value,
                "account_id": account_id,
                "expires_at": expires_at.isoformat(),
                "gateway_synced": False,
                "message": f"{target.value.title()} authentication failed: {auth_error or 'session token is invalid or expired.'}",
            }

        self._runtime_credentials[target.value] = {
            "api_key": api_key,
            "secret_key": secret_key,
            "session_token": token_value,
            "account_id": account_id,
        }
        await self.repo.record_health_check(
            "CONNECTED",
            latency_ms=12.5,
            message=f"{target.value} session activated",
        )
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.AUDIT_EVENT,
                payload={
                    "event_type": "SESSION_ACTIVATED",
                    "session_id": session_id,
                    "broker": target.value,
                    "account_id": account_id,
                    "expires_at": expires_at.isoformat(),
                },
            )
        )
        logger.info("%s session activated: %s", target.value, session_id)
        return {
            "session_id": session_id,
            "status": "CONNECTED",
            "connected": True,
            "broker": target.value,
            "account_id": account_id,
            "expires_at": expires_at.isoformat(),
            "gateway_synced": gateway_synced,
        }

    async def get_session_status(
        self,
        broker: BrokerBackend | str | None = None,
    ) -> dict[str, Any]:
        target = self._normalize_broker(broker)
        persisted = await self.repo.get_active_sessions()
        now = utc_now()
        by_broker: dict[str, dict[str, Any]] = {}

        for row in persisted:
            meta = row.get("metadata") or {}
            name = str(meta.get("broker") or "").lower()
            if name not in {BrokerBackend.BREEZE.value, BrokerBackend.KITE.value}:
                account = str(row.get("account_id") or "").upper()
                name = BrokerBackend.KITE.value if "ZERODHA" in account else BrokerBackend.BREEZE.value
            expires_at = datetime.fromisoformat(row["expires_at"])
            if now >= expires_at:
                await self.repo.expire_session(row["session_id"])
                continue
            if name not in by_broker:
                by_broker[name] = row

        broker_states: dict[str, dict[str, Any]] = {}
        for name in (BrokerBackend.BREEZE.value, BrokerBackend.KITE.value):
            adapter_active = False
            if self.broker_gateway is not None:
                try:
                    adapter_active = self.broker_gateway.provider_is_active(name)
                except Exception:
                    adapter_active = False
            row = by_broker.get(name)
            broker_states[name] = {
                "connected": bool(adapter_active),
                "status": "CONNECTED" if adapter_active else ("PERSISTED" if row else "DISCONNECTED"),
                "account_id": row.get("account_id") if row else None,
                "expires_at": row.get("expires_at") if row else None,
                "token_masked": row.get("session_token_masked") if row else None,
            }

        selected = broker_states[target.value]
        return {
            "status": selected["status"],
            "connected": selected["connected"],
            "broker": target.value,
            "account_id": selected["account_id"],
            "expires_at": selected["expires_at"],
            "token_masked": selected["token_masked"],
            "brokers": broker_states,
        }

    def get_runtime_credentials(
        self,
        broker: BrokerBackend | str | None = None,
    ) -> Optional[dict[str, str]]:
        target = self._normalize_broker(broker)
        return self._runtime_credentials.get(target.value)
