"""Domain Market Data Models.

Uses Decimal for all price and strike measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from libs.contracts.models import utc_now
from services.broker_gateway.domain.enums import FeedInterval, OptionRight
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef


@dataclass(frozen=True, slots=True)
class Quote:
    """Normalized real-time quote snapshot."""

    instrument: BrokerInstrumentRef
    ltp: Decimal
    best_bid_price: Optional[Decimal] = None
    best_bid_qty: Optional[int] = None
    best_ask_price: Optional[Decimal] = None
    best_ask_qty: Optional[int] = None
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Candle:
    """Normalized OHLCV candlestick representation."""

    instrument: BrokerInstrumentRef
    interval: FeedInterval
    start_time: datetime
    end_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    open_interest: Optional[int] = None


@dataclass(frozen=True, slots=True)
class OptionContractQuote:
    """Quote for an individual contract within an option chain."""

    strike_price: Decimal
    right: OptionRight
    ltp: Decimal
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    oi_change: Optional[int] = None


@dataclass(frozen=True, slots=True)
class OptionChainSnapshot:
    """Normalized option chain matrix for an underlying and expiry."""

    underlying: str
    expiry: date
    spot_price: Decimal
    contracts: list[OptionContractQuote]
    timestamp: datetime

