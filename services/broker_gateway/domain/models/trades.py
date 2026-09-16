"""Domain Trade Models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.broker_gateway.domain.enums import OrderSide
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef


@dataclass(frozen=True, slots=True)
class BrokerTradeDetail:
    """Normalized trade fill executed at the exchange."""

    trade_id: str
    broker_order_id: str
    instrument: BrokerInstrumentRef
    side: OrderSide
    quantity: int
    execution_price: Decimal
    trade_time: datetime

