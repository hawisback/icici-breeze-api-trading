"""Domain models, enums, value objects, and standard identifier/datetime helpers.

This module defines the canonical domain contracts used across all microservices,
strictly adhering to Python 3.14 standards, Pydantic v2, and UUIDv7.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


def generate_id() -> str:
    """Generate a globally unique, time-ordered UUIDv7 string.
    
    Python 3.14 includes native uuid.uuid7() implementation conforming to RFC 9562.
    """
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())  # type: ignore[attr-defined]
    # Fallback if accessed via older python runtimes
    return str(uuid.uuid4())


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


# ==============================================================================
# Domain Enums
# ==============================================================================


class SystemMode(str, Enum):
    """System-wide trading safety modes."""
    NORMAL = "NORMAL"
    ENTRY_BLOCKED = "ENTRY_BLOCKED"
    EXIT_ONLY = "EXIT_ONLY"
    HALTED = "HALTED"


class UserRole(str, Enum):
    """Server-owned identity roles for API & WebSocket authorization."""
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    TRADER = "TRADER"
    READ_ONLY = "READ_ONLY"


class TradingMode(str, Enum):
    """Execution mode of the platform or strategy instance."""
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    LIVE = "LIVE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class ProductType(str, Enum):
    CASH = "CASH"
    MARGIN = "MARGIN"
    OPTIONS = "OPTIONS"
    FUTURES = "FUTURES"


class OptionRight(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class SourceType(str, Enum):
    MANUAL = "MANUAL"
    STRATEGY = "STRATEGY"


class TimeInForce(str, Enum):
    DAY = "DAY"
    IOC = "IOC"


class OrderState(str, Enum):
    """Canonical 14-state OMS state machine."""
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    RISK_REJECTED = "RISK_REJECTED"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED_SAFE = "FAILED_SAFE"


# ==============================================================================
# Core Domain Value Objects & Entities
# ==============================================================================


class BaseDomainModel(BaseModel):
    """Base model enforcing immutability and serialization standards."""
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        from_attributes=True,
    )


class Instrument(BaseDomainModel):
    """Contract definition for equities, futures, and option contracts."""
    instrument_id: str
    broker: str = "ICICI_BREEZE"
    exchange: str = "NFO"  # NSE, NFO, BSE
    segment: str = "OPTIONS"  # EQUITY, FUTURES, OPTIONS
    underlying: str  # e.g., NIFTY, BANKNIFTY
    stock_code: str  # Breeze stock code, e.g. "NIFTY"
    expiry: Optional[str] = None  # YYYY-MM-DD
    strike: Optional[float] = None
    option_right: Optional[OptionRight] = None
    lot_size: int = 1
    tick_size: float = 0.05
    broker_token: Optional[str] = None
    tradable: bool = True
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


class OrderIntent(BaseDomainModel):
    """Order request intent initiated by a Strategy or Manual UI Ticket."""
    intent_id: str = Field(default_factory=generate_id)
    correlation_id: str = Field(default_factory=generate_id)
    strategy_instance_id: Optional[str] = None
    source: SourceType = SourceType.MANUAL
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.LIMIT
    quantity: int
    price: float
    trigger_price: Optional[float] = None
    product: ProductType = ProductType.OPTIONS
    time_in_force: TimeInForce = TimeInForce.DAY
    trading_mode: TradingMode = TradingMode.PAPER
    reduce_only: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class RiskDecision(BaseDomainModel):
    """Decision evaluated by the Risk Service for an OrderIntent."""
    decision_id: str = Field(default_factory=generate_id)
    intent_id: str
    approved: bool
    rule_name: Optional[str] = None
    reason: Optional[str] = None
    system_mode: SystemMode = SystemMode.NORMAL
    evaluated_at: datetime = Field(default_factory=utc_now)


class BrokerOrder(BaseDomainModel):
    """Broker-tracked order entity managed by OMS."""
    order_id: str = Field(default_factory=generate_id)
    intent_id: str
    client_order_id: str
    broker_order_id: Optional[str] = None
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    filled_quantity: int = 0
    remaining_quantity: int
    price: float
    average_price: float = 0.0
    status: OrderState = OrderState.CREATED
    status_message: Optional[str] = None
    trading_mode: TradingMode = TradingMode.PAPER
    reduce_only: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OrderEvent(BaseDomainModel):
    """Audit log entry for an order state transition."""
    event_id: str = Field(default_factory=generate_id)
    order_id: str
    from_state: Optional[OrderState] = None
    to_state: OrderState
    reason: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class Execution(BaseDomainModel):
    """Trade fill representation."""
    execution_id: str = Field(default_factory=generate_id)
    order_id: str
    broker_execution_id: Optional[str] = None
    instrument_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    fee: float = 0.0
    execution_time: datetime = Field(default_factory=utc_now)


class Position(BaseDomainModel):
    """Aggregated portfolio position per instrument."""
    position_id: str = Field(default_factory=generate_id)
    instrument_id: str
    symbol: str
    quantity: int = 0  # Net quantity (> 0 long, < 0 short)
    buy_quantity: int = 0
    sell_quantity: int = 0
    buy_value: float = 0.0
    sell_value: float = 0.0
    average_price: float = 0.0
    current_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    trading_mode: TradingMode = TradingMode.PAPER
    updated_at: datetime = Field(default_factory=utc_now)


class PnLSnapshot(BaseDomainModel):
    """Point-in-time total portfolio P&L snapshot."""
    snapshot_id: str = Field(default_factory=generate_id)
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    day_pnl: float
    open_positions_count: int
    timestamp: datetime = Field(default_factory=utc_now)


class Quote(BaseDomainModel):
    """Normalized live market quote."""
    source: str = "UNKNOWN"
    instrument_id: str
    symbol: str
    last_price: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    open_interest: int = 0
    best_bid: float = 0.0
    best_bid_qty: int = 0
    best_ask: float = 0.0
    best_ask_qty: int = 0
    change_pct: float = 0.0
    timestamp: datetime = Field(default_factory=utc_now)


class Candle(BaseDomainModel):
    """OHLCV candlestick."""
    instrument_id: str
    interval: str  # "1m", "5m", "15m", "1D"
    start_time: datetime
    end_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    open_interest: Optional[int] = 0
    source: str = "BREEZE"


class Signal(BaseDomainModel):
    """Signal produced by a strategy calculation."""
    signal_id: str = Field(default_factory=generate_id)
    strategy_instance_id: str
    symbol: str
    side: OrderSide
    suggested_price: Optional[float] = None
    suggested_quantity: int
    confidence: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class UserPrincipal(BaseDomainModel):
    """Authenticated user context identity."""
    user_id: str
    username: str
    role: UserRole
    is_active: bool = True
