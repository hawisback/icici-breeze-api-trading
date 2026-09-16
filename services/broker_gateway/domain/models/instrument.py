"""Domain Instrument Models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from services.broker_gateway.domain.enums import Exchange, OptionRight, ProductType


@dataclass(frozen=True, slots=True)
class BrokerInstrumentRef:
    """Normalized broker reference provided by the Instrument Service."""

    internal_instrument_id: str
    exchange: Exchange
    stock_code: str
    product_type: ProductType
    expiry: Optional[date] = None
    strike: Optional[Decimal] = None
    option_right: Optional[OptionRight] = None
    stock_token: Optional[str] = None

