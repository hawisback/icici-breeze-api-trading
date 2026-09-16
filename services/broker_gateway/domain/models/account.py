"""Domain Account & Funds Models.

Strictly uses Decimal for all monetary balances and margins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.broker_gateway.domain.enums import Exchange


@dataclass(frozen=True, slots=True)
class FundsSnapshot:
    """Normalized funds and cash balance snapshot from the broker."""

    available_margin: Decimal
    total_cash: Decimal
    used_margin: Decimal
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class MarginSnapshot:
    """Normalized exchange-specific margin requirement snapshot."""

    exchange: Exchange
    total_margin: Decimal
    required_margin: Decimal
    available_margin: Decimal
    timestamp: datetime

