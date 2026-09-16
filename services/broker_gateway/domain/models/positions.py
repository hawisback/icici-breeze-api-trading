"""Domain Position Models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef


@dataclass(frozen=True, slots=True)
class BrokerPositionDetail:
    """Normalized position holding reported by the broker."""

    instrument: BrokerInstrumentRef
    quantity: int
    buy_quantity: int
    sell_quantity: int
    average_price: Decimal
    ltp: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal
    updated_at: datetime

