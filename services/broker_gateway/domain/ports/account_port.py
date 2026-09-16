"""Broker Account Port Protocol.
"""

from __future__ import annotations

from typing import Protocol

from services.broker_gateway.domain.enums import Exchange
from services.broker_gateway.domain.models.account import FundsSnapshot, MarginSnapshot


class BrokerAccountPort(Protocol):
    """Port for querying account funds and exchange margins."""

    async def get_funds(self) -> FundsSnapshot:
        """Fetch available margin and cash balances."""
        ...

    async def get_margin(self, exchange: Exchange) -> MarginSnapshot:
        """Fetch exchange-specific margin requirements."""
        ...

