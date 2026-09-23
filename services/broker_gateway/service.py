"""Broker Gateway Service routing normalized requests across paper, Breeze, and Kite."""

from __future__ import annotations

import logging
from typing import Optional

from libs.broker_models.adapter import (
    BrokerAdapter,
    BrokerFunds,
    BrokerOrderRequest,
    BrokerOrderResponse,
    BrokerPositionResponse,
    BrokerTradeResponse,
)
from libs.config.settings import BrokerBackend, PlatformSettings, get_settings
from libs.contracts.models import TradingMode
from services.broker_gateway.application.services.broker_service import BrokerApplicationService
from services.broker_gateway.icici_breeze_adapter import IciciBreezeAdapter
from services.broker_gateway.paper_adapter import PaperBrokerAdapter
from services.broker_gateway.zerodha_kite_adapter import ZerodhaKiteAdapter

logger = logging.getLogger(__name__)


class BrokerGatewayService:
    """Broker-neutral routing boundary.

    Execution ownership is explicit per order. Read-side provider order is
    capability-specific so Breeze and Kite can remain active simultaneously.
    """

    def __init__(
        self,
        paper_adapter: Optional[PaperBrokerAdapter] = None,
        breeze_adapter: Optional[IciciBreezeAdapter] = None,
        kite_adapter: Optional[ZerodhaKiteAdapter] = None,
        settings: Optional[PlatformSettings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.paper_adapter = paper_adapter or PaperBrokerAdapter()
        self.breeze_adapter = breeze_adapter or IciciBreezeAdapter()
        self.kite_adapter = kite_adapter or ZerodhaKiteAdapter(
            api_key=_secret(self.settings.kite_api_key),
            api_secret=_secret(self.settings.kite_api_secret),
            product=self.settings.kite_product,
        )

    async def initialize(self) -> None:
        if hasattr(self.breeze_adapter, "initialize"):
            await self.breeze_adapter.initialize()
        if hasattr(self.kite_adapter, "initialize"):
            await self.kite_adapter.initialize()

    @property
    def broker_backend(self) -> BrokerBackend:
        """Default execution broker."""
        return self.settings.broker_backend

    @property
    def active_adapter(self) -> BrokerAdapter:
        """Backward-compatible alias for the default execution adapter."""
        return self.get_broker_adapter(self.broker_backend)

    @property
    def active_broker_name(self) -> str:
        return self.broker_backend.value

    @property
    def clean_breeze_service(self) -> BrokerApplicationService:
        """Compatibility access to Breeze's clean-architecture service."""
        return self.breeze_adapter.clean_service

    def get_broker_adapter(self, broker: BrokerBackend | str | None = None) -> BrokerAdapter:
        value = broker.value if isinstance(broker, BrokerBackend) else str(broker or self.broker_backend.value).lower()
        if value == BrokerBackend.KITE.value:
            return self.kite_adapter
        if value == BrokerBackend.BREEZE.value:
            return self.breeze_adapter
        raise ValueError(f"Unsupported broker: {broker}")

    def provider_is_active(self, broker: BrokerBackend | str) -> bool:
        adapter = self.get_broker_adapter(broker)
        return bool(getattr(adapter, "is_active", False))

    def provider_order(self, capability: str) -> tuple[BrokerBackend, ...]:
        """Return configured primary/fallback order for a read capability."""
        key = capability.strip().lower()
        if key in {"live", "quotes", "market_data"}:
            primary, secondary = self.settings.live_data_primary, self.settings.live_data_secondary
        elif key in {"historical", "history"}:
            primary, secondary = self.settings.historical_primary, self.settings.historical_secondary
        elif key in {"option_chain", "chain", "options"}:
            primary, secondary = self.settings.option_chain_primary, self.settings.option_chain_secondary
        else:
            primary, secondary = self.broker_backend, (
                BrokerBackend.KITE if self.broker_backend == BrokerBackend.BREEZE else BrokerBackend.BREEZE
            )
        return (primary,) if primary == secondary else (primary, secondary)

    async def authenticate_broker(
        self,
        broker: BrokerBackend | str,
        *,
        api_key: str,
        secret_key: str,
        session_token: str = "",
        access_token: Optional[str] = None,
    ) -> bool:
        adapter = self.get_broker_adapter(broker)
        if access_token and hasattr(adapter, "authenticate_access_token"):
            return bool(
                await adapter.authenticate_access_token(
                    api_key=api_key,
                    access_token=access_token,
                )
            )
        return bool(
            await adapter.authenticate(
                api_key=api_key,
                secret_key=secret_key,
                session_token=session_token,
            )
        )

    def get_adapter(
        self,
        mode: TradingMode,
        broker: BrokerBackend | str | None = None,
    ) -> BrokerAdapter:
        if mode == TradingMode.LIVE:
            return self.get_broker_adapter(broker)
        return self.paper_adapter

    async def get_funds(
        self,
        mode: TradingMode = TradingMode.PAPER,
        broker: BrokerBackend | str | None = None,
    ) -> BrokerFunds:
        return await self.get_adapter(mode, broker).get_funds()

    async def place_order(
        self,
        request: BrokerOrderRequest,
        mode: TradingMode = TradingMode.PAPER,
        broker: BrokerBackend | str | None = None,
    ) -> BrokerOrderResponse:
        return await self.get_adapter(mode, broker).place_order(request)

    async def modify_order(
        self,
        broker_order_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
        mode: TradingMode = TradingMode.PAPER,
        broker: BrokerBackend | str | None = None,
    ) -> BrokerOrderResponse:
        return await self.get_adapter(mode, broker).modify_order(broker_order_id, quantity, price)

    async def cancel_order(
        self,
        broker_order_id: str,
        mode: TradingMode = TradingMode.PAPER,
        broker: BrokerBackend | str | None = None,
    ) -> BrokerOrderResponse:
        return await self.get_adapter(mode, broker).cancel_order(broker_order_id)

    async def get_order_status(
        self,
        broker_order_id: str,
        mode: TradingMode = TradingMode.LIVE,
        broker: BrokerBackend | str | None = None,
    ) -> Optional[BrokerOrderResponse]:
        adapter = self.get_adapter(mode, broker)
        return await adapter.get_order_status(broker_order_id)

    async def get_positions(
        self,
        mode: TradingMode = TradingMode.PAPER,
        broker: BrokerBackend | str | None = None,
    ) -> list[BrokerPositionResponse]:
        return await self.get_adapter(mode, broker).get_positions()

    async def get_trades(
        self,
        mode: TradingMode = TradingMode.PAPER,
        broker: BrokerBackend | str | None = None,
    ) -> list[BrokerTradeResponse]:
        return await self.get_adapter(mode, broker).get_trades()


def _secret(value: object) -> str:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")
