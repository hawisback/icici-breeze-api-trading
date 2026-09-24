"""Breeze Trading Adapter implementing BrokerTradingPort.

Enforces:
- Serialized broker writes through client write lock.
- Rate-limiting via BrokerRateLimiter.
- Off-loop execution via SdkRunner.
- Bounded timeouts with clean exception propagation.
- Both raw and normalized status preservation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Optional
import uuid

from libs.contracts.models import OrderSide as PlatformOrderSide
from services.broker_gateway.domain.enums import (
    BrokerWriteStatus,
    Exchange,
    OptionRight,
    OrderSide,
    ProductType,
)
from services.broker_gateway.domain.errors import (
    BrokerBaseError,
    BrokerOrderRejectedError,
    BrokerTimeoutError,
)
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef
from services.broker_gateway.domain.models.orders import (
    BrokerOrderAcknowledgement,
    BrokerOrderDetail,
    BrokerOrderRequest,
    CancelBrokerOrderRequest,
    ModifyBrokerOrderRequest,
    SquareOffRequest,
)
from services.broker_gateway.domain.models.positions import BrokerPositionDetail
from services.broker_gateway.domain.models.trades import BrokerTradeDetail
from services.broker_gateway.domain.ports.trading_port import BrokerTradingPort
from services.broker_gateway.infrastructure.icici.breeze_client import BreezeClientManager
from services.broker_gateway.infrastructure.icici.datetime_mapper import (
    parse_breeze_datetime,
    to_breeze_date_str,
    to_breeze_iso,
)
from services.broker_gateway.infrastructure.icici.request_mapper import (
    map_cancel_order_request,
    map_exchange_to_breeze,
    map_modify_order_request,
    map_place_order_request,
    map_square_off_request,
)
from services.broker_gateway.infrastructure.icici.response_mapper import BreezeResponseValidator
from services.broker_gateway.infrastructure.icici.status_mapper import normalize_breeze_order_status
from services.broker_gateway.infrastructure.rate_limit.policies import BrokerRateLimiter

logger = logging.getLogger(__name__)


def _parse_optional_expiry(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for candidate in (text[:10], text[:11]):
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


class BreezeTradingAdapter(BrokerTradingPort):
    """Adapter executing live orders and book reconciliation against ICICI Breeze."""

    def __init__(
        self,
        client_manager: BreezeClientManager,
        rate_limiter: Optional[BrokerRateLimiter] = None,
    ) -> None:
        self.client_manager = client_manager
        self.rate_limiter = rate_limiter or BrokerRateLimiter()

    async def place_order(self, request: BrokerOrderRequest) -> BrokerOrderAcknowledgement:
        """Submit a new LIMIT or STOP_LIMIT order to Breeze with lock and rate-limiting."""
        await self.rate_limiter.acquire_write()
        params = map_place_order_request(request)

        async with self.client_manager.write_lock:
            sdk = self.client_manager.get_sdk_client()
            try:
                raw_resp = await self.client_manager.sdk_runner.run(
                    lambda: sdk.place_order(**params),
                    timeout_sec=8.0,
                )
            except TimeoutError as exc:
                logger.error("Breeze place_order timed out for request %s", request.request_id)
                raise BrokerTimeoutError(
                    f"Breeze place_order call timed out for request {request.request_id}."
                ) from exc
            except Exception as exc:
                if isinstance(exc, BrokerBaseError):
                    raise
                logger.error("Breeze place_order error for request %s: %s", request.request_id, exc)
                raise

        data = BreezeResponseValidator.unwrap_success(raw_resp)
        broker_order_id = None
        if isinstance(data, dict):
            broker_order_id = str(data.get("order_id") or data.get("order_no") or "")
        elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            broker_order_id = str(data[0].get("order_id") or data[0].get("order_no") or "")

        now = datetime.now(timezone.utc)
        return BrokerOrderAcknowledgement(
            request_id=request.request_id,
            client_reference=request.client_reference,
            broker_order_id=broker_order_id,
            status=BrokerWriteStatus.ACKNOWLEDGED,
            message="Order placed successfully with broker",
            acknowledged_at=now,
        )

    async def modify_order(self, request: ModifyBrokerOrderRequest) -> BrokerOrderAcknowledgement:
        """Modify an existing working order in Breeze."""
        await self.rate_limiter.acquire_write()
        params = map_modify_order_request(request)

        async with self.client_manager.write_lock:
            sdk = self.client_manager.get_sdk_client()
            try:
                raw_resp = await self.client_manager.sdk_runner.run(
                    lambda: sdk.modify_order(**params),
                    timeout_sec=8.0,
                )
            except TimeoutError as exc:
                logger.error("Breeze modify_order timed out for request %s", request.request_id)
                raise BrokerTimeoutError(
                    f"Breeze modify_order call timed out for request {request.request_id}."
                ) from exc

        data = BreezeResponseValidator.unwrap_success(raw_resp)
        now = datetime.now(timezone.utc)
        return BrokerOrderAcknowledgement(
            request_id=request.request_id,
            client_reference=request.broker_order_id,
            broker_order_id=request.broker_order_id,
            status=BrokerWriteStatus.ACKNOWLEDGED,
            message="Order modified successfully",
            acknowledged_at=now,
        )

    async def cancel_order(self, request: CancelBrokerOrderRequest) -> BrokerOrderAcknowledgement:
        """Cancel an open working order in Breeze."""
        await self.rate_limiter.acquire_write()
        params = map_cancel_order_request(request)

        async with self.client_manager.write_lock:
            sdk = self.client_manager.get_sdk_client()
            try:
                raw_resp = await self.client_manager.sdk_runner.run(
                    lambda: sdk.cancel_order(**params),
                    timeout_sec=8.0,
                )
            except TimeoutError as exc:
                logger.error("Breeze cancel_order timed out for request %s", request.request_id)
                raise BrokerTimeoutError(
                    f"Breeze cancel_order call timed out for request {request.request_id}."
                ) from exc

        data = BreezeResponseValidator.unwrap_success(raw_resp)
        now = datetime.now(timezone.utc)
        return BrokerOrderAcknowledgement(
            request_id=request.request_id,
            client_reference=request.broker_order_id,
            broker_order_id=request.broker_order_id,
            status=BrokerWriteStatus.ACKNOWLEDGED,
            message="Order cancelled successfully",
            acknowledged_at=now,
        )

    async def square_off(self, request: SquareOffRequest) -> BrokerOrderAcknowledgement:
        """Square off a position in Breeze."""
        await self.rate_limiter.acquire_write()
        params = map_square_off_request(request)

        async with self.client_manager.write_lock:
            sdk = self.client_manager.get_sdk_client()
            try:
                raw_resp = await self.client_manager.sdk_runner.run(
                    lambda: sdk.square_off(**params),
                    timeout_sec=8.0,
                )
            except TimeoutError as exc:
                logger.error("Breeze square_off timed out for request %s", request.request_id)
                raise BrokerTimeoutError(
                    f"Breeze square_off call timed out for request {request.request_id}."
                ) from exc

        data = BreezeResponseValidator.unwrap_success(raw_resp)
        now = datetime.now(timezone.utc)
        return BrokerOrderAcknowledgement(
            request_id=request.request_id,
            client_reference=request.request_id,
            broker_order_id=str(data.get("order_id") or "") if isinstance(data, dict) else None,
            status=BrokerWriteStatus.ACKNOWLEDGED,
            message="Position squared off successfully",
            acknowledged_at=now,
        )

    async def get_orders(self) -> list[BrokerOrderDetail]:
        """Fetch all orders from Breeze order book for the current day."""
        await self.rate_limiter.acquire_read()
        sdk = self.client_manager.get_sdk_client()
        today = datetime.now(timezone.utc).date()
        today_iso = f"{to_breeze_date_str(today)}T06:00:00.000Z"

        raw_resp = await self.client_manager.sdk_runner.run(
            lambda: sdk.get_order_list(
                exchange_code="NFO",
                from_date=today_iso,
                to_date=today_iso,
            ),
            timeout_sec=12.0,
        )
        data = BreezeResponseValidator.unwrap_success(raw_resp)
        rows: list[dict[str, Any]] = data if isinstance(data, list) else []

        orders: list[BrokerOrderDetail] = []
        for row in rows:
            orders.append(self._parse_order_detail(row))
        return orders

    async def get_order_detail(self, broker_order_id: str) -> Optional[BrokerOrderDetail]:
        """Fetch specific order details from Breeze."""
        await self.rate_limiter.acquire_read()
        sdk = self.client_manager.get_sdk_client()

        raw_resp = await self.client_manager.sdk_runner.run(
            lambda: sdk.get_order_detail(
                exchange_code="NFO",
                order_id=broker_order_id,
            ),
            timeout_sec=10.0,
        )
        data = BreezeResponseValidator.unwrap_success(raw_resp)
        if isinstance(data, list) and len(data) > 0:
            return self._parse_order_detail(data[0])
        elif isinstance(data, dict) and data:
            return self._parse_order_detail(data)
        return None

    async def get_trades(self) -> list[BrokerTradeDetail]:
        """Fetch executed trade fills for the day."""
        await self.rate_limiter.acquire_read()
        sdk = self.client_manager.get_sdk_client()
        today = datetime.now(timezone.utc).date()
        today_iso = f"{to_breeze_date_str(today)}T06:00:00.000Z"

        raw_resp = await self.client_manager.sdk_runner.run(
            lambda: sdk.get_trade_list(
                from_date=today_iso,
                to_date=today_iso,
                exchange_code="NFO",
                product_type="options",
            ),
            timeout_sec=12.0,
        )
        data = BreezeResponseValidator.unwrap_success(raw_resp)
        rows: list[dict[str, Any]] = data if isinstance(data, list) else []

        trades: list[BrokerTradeDetail] = []
        for row in rows:
            trade_id = str(row.get("trade_id") or row.get("trade_no") or uuid.uuid4())
            order_id = str(row.get("order_id") or row.get("order_no") or "")
            side_raw = str(row.get("action", "")).lower()
            side = OrderSide.BUY if "buy" in side_raw else OrderSide.SELL
            qty = int(row.get("quantity", 0))
            price = Decimal(str(row.get("execution_price") or row.get("price") or "0"))
            trade_time = parse_breeze_datetime(str(row.get("trade_date") or row.get("order_date") or ""))

            right_raw = str(row.get("right", "")).lower()
            option_right = (
                OptionRight.CALL
                if "call" in right_raw
                else OptionRight.PUT
                if "put" in right_raw
                else None
            )
            inst = BrokerInstrumentRef(
                internal_instrument_id=uuid.uuid4(),
                exchange=Exchange.NFO,
                stock_code=str(row.get("stock_code", "")),
                product_type=ProductType.OPTIONS,
                expiry=_parse_optional_expiry(
                    row.get("expiry_date") or row.get("expiry")
                ),
                strike=Decimal(str(row.get("strike_price", 0))) if row.get("strike_price") else None,
                option_right=option_right,
                stock_token=None,
            )
            trades.append(
                BrokerTradeDetail(
                    trade_id=trade_id,
                    broker_order_id=order_id,
                    instrument=inst,
                    side=side,
                    quantity=qty,
                    execution_price=price,
                    trade_time=trade_time,
                )
            )
        return trades

    async def get_positions(self) -> list[BrokerPositionDetail]:
        """Fetch open and closed portfolio positions from Breeze."""
        await self.rate_limiter.acquire_read()
        sdk = self.client_manager.get_sdk_client()

        raw_resp = await self.client_manager.sdk_runner.run(
            lambda: sdk.get_portfolio_positions(),
            timeout_sec=12.0,
        )
        data = BreezeResponseValidator.unwrap_success(raw_resp)
        rows: list[dict[str, Any]] = data if isinstance(data, list) else []

        positions: list[BrokerPositionDetail] = []
        for row in rows:
            qty = int(row.get("quantity", 0))
            buy_qty = int(row.get("buy_quantity", 0))
            sell_qty = int(row.get("sell_quantity", 0))
            avg_price = Decimal(str(row.get("average_price") or row.get("buy_price") or "0"))
            ltp = Decimal(str(row.get("ltp") or row.get("current_price") or "0"))
            realized = Decimal(str(row.get("realized_profit") or "0"))
            unrealized = Decimal(str(row.get("unrealized_profit") or "0"))
            right_raw = str(row.get("right", "")).lower()
            option_right = (
                OptionRight.CALL
                if "call" in right_raw
                else OptionRight.PUT
                if "put" in right_raw
                else None
            )

            inst = BrokerInstrumentRef(
                internal_instrument_id=uuid.uuid4(),
                exchange=Exchange.NFO,
                stock_code=str(row.get("stock_code", "")),
                product_type=ProductType.OPTIONS,
                expiry=_parse_optional_expiry(
                    row.get("expiry_date") or row.get("expiry")
                ),
                strike=Decimal(str(row.get("strike_price", 0))) if row.get("strike_price") else None,
                option_right=option_right,
                stock_token=None,
            )
            positions.append(
                BrokerPositionDetail(
                    instrument=inst,
                    quantity=qty,
                    buy_quantity=buy_qty,
                    sell_quantity=sell_qty,
                    average_price=avg_price,
                    ltp=ltp,
                    realized_pnl=realized,
                    unrealized_pnl=unrealized,
                    total_pnl=realized + unrealized,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        return positions

    def _parse_order_detail(self, row: dict[str, Any]) -> BrokerOrderDetail:
        """Normalize raw Breeze order dictionary into BrokerOrderDetail."""
        raw_status = str(row.get("status") or row.get("order_status") or "UNKNOWN")
        norm_status = normalize_breeze_order_status(raw_status).value

        side_raw = str(row.get("action", "")).lower()
        side = OrderSide.BUY if "buy" in side_raw else OrderSide.SELL

        inst = BrokerInstrumentRef(
            internal_instrument_id=uuid.uuid4(),
            exchange=Exchange.NFO if str(row.get("exchange_code", "")).upper() == "NFO" else Exchange.NSE,
            stock_code=str(row.get("stock_code", "")),
            product_type=ProductType.OPTIONS,
            expiry=None,
            strike=Decimal(str(row.get("strike_price", 0))) if row.get("strike_price") else None,
            option_right=OptionRight.CALL if "call" in str(row.get("right", "")).lower() else None,
            stock_token=None,
        )

        order_dt = parse_breeze_datetime(str(row.get("order_date") or row.get("order_time") or ""))
        update_dt = parse_breeze_datetime(str(row.get("update_time") or row.get("order_date") or ""))

        return BrokerOrderDetail(
            broker_order_id=str(row.get("order_id") or row.get("order_no") or ""),
            client_reference=row.get("user_remark"),
            exchange_order_id=row.get("exchange_order_id"),
            instrument=inst,
            side=side,
            quantity=int(row.get("quantity", 0)),
            filled_quantity=int(row.get("executed_quantity") or row.get("cancelled_quantity") or 0),
            price=Decimal(str(row.get("price", "0"))),
            average_price=Decimal(str(row.get("average_price") or row.get("price") or "0")),
            raw_status=raw_status,
            normalized_status=norm_status,
            order_time=order_dt,
            update_time=update_dt,
        )

