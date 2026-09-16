"""Domain Order Models.

Strictly uses Decimal for all prices and prevents binary float drift.
Normal market orders are forbidden: only LIMIT and STOP_LIMIT are permitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from services.broker_gateway.domain.enums import (
    BrokerWriteStatus,
    OrderSide,
    OrderStyle,
    OrderValidity,
)
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef


@dataclass(frozen=True, slots=True)
class BrokerOrderRequest:
    """Outbound order placement request sent from Execution Service to Broker Gateway."""

    request_id: str
    account_id: str
    instrument: BrokerInstrumentRef
    side: OrderSide
    quantity: int
    order_style: OrderStyle
    limit_price: Optional[Decimal]
    stop_price: Optional[Decimal]
    validity: OrderValidity
    client_reference: str
    user_remark: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ModifyBrokerOrderRequest:
    """Order modification request."""

    request_id: str
    broker_order_id: str
    quantity: Optional[int] = None
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None


@dataclass(frozen=True, slots=True)
class CancelBrokerOrderRequest:
    """Order cancellation request."""

    request_id: str
    broker_order_id: str


@dataclass(frozen=True, slots=True)
class SquareOffRequest:
    """Explicit square-off request for a position."""

    request_id: str
    instrument: BrokerInstrumentRef
    quantity: int
    limit_price: Optional[Decimal] = None


@dataclass(frozen=True, slots=True)
class BrokerOrderAcknowledgement:
    """Immediate acknowledgement returned by the broker upon placement/modify/cancel."""

    request_id: str
    client_reference: str
    broker_order_id: Optional[str]
    status: BrokerWriteStatus
    message: Optional[str]
    acknowledged_at: datetime


@dataclass(frozen=True, slots=True)
class BrokerOrderDetail:
    """Normalized order query detail retrieved from broker book."""

    broker_order_id: str
    client_reference: Optional[str]
    exchange_order_id: Optional[str]
    instrument: BrokerInstrumentRef
    side: OrderSide
    quantity: int
    filled_quantity: int
    price: Decimal
    average_price: Decimal
    raw_status: str
    normalized_status: str
    order_time: datetime
    update_time: datetime

