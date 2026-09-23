"""Abstract broker adapter protocol and normalized broker request/response models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from libs.contracts.models import utc_now


class BrokerFunds(BaseModel):
    """Normalized funds and margin balances."""
    available_margin: float
    total_cash: float
    used_margin: float
    unrealized_m2m: float = 0.0
    currency: str = "INR"

    model_config = ConfigDict(frozen=True)


class BrokerOrderRequest(BaseModel):
    """Normalized order request to be sent through a BrokerAdapter."""
    client_order_id: str
    stock_code: str
    exchange_code: str = "NFO"  # NFO, NSE
    product: str = "options"  # options, futures, cash
    action: str  # buy, sell
    order_type: str  # limit, market
    quantity: int
    price: float
    validity: str = "day"
    strike_price: Optional[float] = None
    right: Optional[str] = None  # call, put
    expiry_date: Optional[str] = None  # YYYY-MM-DD
    user_remark: Optional[str] = None

    model_config = ConfigDict(frozen=True)


class BrokerOrderResponse(BaseModel):
    """Normalized response from broker order placement, modification, or cancellation."""
    success: bool
    broker_order_id: Optional[str] = None
    client_order_id: str
    status: str  # "PLACED", "REJECTED", "CANCELLED", "MODIFIED", "UNKNOWN"
    message: Optional[str] = None
    error_code: Optional[str] = None
    filled_quantity: int = 0
    average_price: float = 0.0
    timestamp: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(frozen=True)


class BrokerPositionResponse(BaseModel):
    """Normalized position reported by broker."""
    stock_code: str
    exchange_code: str
    product_type: str
    quantity: int
    average_price: float
    ltp: float
    pnl: float
    strike_price: Optional[float] = None
    right: Optional[str] = None
    expiry_date: Optional[str] = None

    model_config = ConfigDict(frozen=True)


class BrokerTradeResponse(BaseModel):
    """Normalized trade execution record reported by broker."""
    trade_id: str
    broker_order_id: str
    client_order_id: Optional[str] = None
    stock_code: str
    exchange_code: str
    action: str
    quantity: int
    price: float
    trade_time: datetime

    model_config = ConfigDict(frozen=True)


class BrokerAdapter(Protocol):
    """Protocol implemented by ICICI Breeze and Paper Broker adapters."""

    async def authenticate(self, api_key: str, secret_key: str, session_token: str) -> bool:
        """Authenticate session with the broker."""
        ...

    async def get_funds(self) -> BrokerFunds:
        """Fetch account balances and margin limits."""
        ...

    async def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResponse:
        """Submit new order to broker."""
        ...

    async def modify_order(
        self,
        broker_order_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
    ) -> BrokerOrderResponse:
        """Modify an existing open order."""
        ...

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResponse:
        """Cancel an open order."""
        ...

    async def get_order_status(self, broker_order_id: str) -> Optional[BrokerOrderResponse]:
        """Query current status of an order."""
        ...

    async def find_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrderResponse]:
        """Recover a broker order from the persisted client id/tag after an ambiguous submit."""
        ...

    async def get_positions(self) -> list[BrokerPositionResponse]:
        """Fetch all open/closed positions."""
        ...

    async def get_trades(self) -> list[BrokerTradeResponse]:
        """Fetch executions and trades for the trading day."""
        ...

