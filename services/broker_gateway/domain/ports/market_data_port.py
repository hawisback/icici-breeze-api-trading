"""Broker Market Data Port Protocol.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Protocol

from services.broker_gateway.domain.enums import FeedInterval
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef
from services.broker_gateway.domain.models.market_data import (
    Candle,
    OptionChainSnapshot,
    Quote,
)


class BrokerMarketDataPort(Protocol):
    """Port for fetching quotes, historical candles, and option chain data."""

    async def get_quote(self, instrument: BrokerInstrumentRef) -> Quote:
        """Fetch current quote for an instrument."""
        ...

    async def get_historical(
        self,
        instrument: BrokerInstrumentRef,
        interval: FeedInterval,
        from_date: datetime,
        to_date: datetime,
    ) -> list[Candle]:
        """Fetch historical candlestick series."""
        ...

    async def get_option_chain(
        self,
        underlying: str,
        expiry: date,
        exchange: str = "NFO",
    ) -> OptionChainSnapshot:
        """Fetch full option chain quote snapshot."""
        ...

