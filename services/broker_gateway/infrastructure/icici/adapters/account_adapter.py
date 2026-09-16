"""Breeze Account Adapter implementing BrokerAccountPort.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Optional

from services.broker_gateway.domain.enums import Exchange
from services.broker_gateway.domain.models.account import FundsSnapshot, MarginSnapshot
from services.broker_gateway.domain.ports.account_port import BrokerAccountPort
from services.broker_gateway.infrastructure.icici.breeze_client import BreezeClientManager
from services.broker_gateway.infrastructure.icici.response_mapper import BreezeResponseValidator
from services.broker_gateway.infrastructure.rate_limit.policies import BrokerRateLimiter

logger = logging.getLogger(__name__)


class BreezeAccountAdapter(BrokerAccountPort):
    """Adapter for querying account funds and exchange margins from ICICI Breeze."""

    def __init__(
        self,
        client_manager: BreezeClientManager,
        rate_limiter: Optional[BrokerRateLimiter] = None,
    ) -> None:
        self.client_manager = client_manager
        self.rate_limiter = rate_limiter or BrokerRateLimiter()

    async def get_funds(self) -> FundsSnapshot:
        """Fetch available margin and cash balances from Breeze API."""
        await self.rate_limiter.acquire_read()
        sdk = self.client_manager.get_sdk_client()

        raw_resp = await self.client_manager.sdk_runner.run(
            lambda: sdk.get_funds(),
            timeout_sec=10.0,
        )
        data = BreezeResponseValidator.unwrap_success(raw_resp)

        # Breeze get_funds may return a dict or a list containing a dict
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        elif not isinstance(data, dict):
            data = {}

        # Parse balances into Decimal with exact Breeze keys and legacy fallbacks
        bank_balance = Decimal(
            str(
                data.get("total_bank_balance")
                or data.get("bank_balance")
                or data.get("allocated_fno")
                or data.get("allocated_amount")
                or "0"
            )
        )
        cash_available = Decimal(
            str(
                data.get("unallocated_balance")
                or data.get("cash_available")
                or data.get("unallocated_amount")
                or "0"
            )
        )
        margin_used = Decimal(
            str(
                data.get("block_by_trade_balance")
                or data.get("margin_used")
                or data.get("block_amount")
                or "0"
            )
        )

        # available_margin is unallocated cash or total available
        avail_margin = cash_available if cash_available > 0 else bank_balance

        return FundsSnapshot(
            available_margin=avail_margin,
            total_cash=bank_balance + cash_available,
            used_margin=margin_used,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_margin(self, exchange: Exchange) -> MarginSnapshot:
        """Fetch exchange-specific margin requirements."""
        await self.rate_limiter.acquire_read()
        sdk = self.client_manager.get_sdk_client()

        raw_resp = await self.client_manager.sdk_runner.run(
            lambda: sdk.get_margin(exchange_code=exchange.value),
            timeout_sec=10.0,
        )
        data = BreezeResponseValidator.unwrap_success(raw_resp)
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        elif not isinstance(data, dict):
            data = {}

        total_margin = Decimal(str(data.get("total_margin") or data.get("total_margin_available") or "0"))
        required_margin = Decimal(str(data.get("required_margin") or data.get("margin_required") or "0"))
        available_margin = Decimal(str(data.get("available_margin") or data.get("free_margin") or "0"))

        return MarginSnapshot(
            exchange=exchange,
            total_margin=total_margin,
            required_margin=required_margin,
            available_margin=available_margin,
            timestamp=datetime.now(timezone.utc),
        )

