"""High-fidelity Paper Broker adapter simulating real exchange behavior.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Optional

from libs.broker_models.adapter import (
    BrokerAdapter,
    BrokerFunds,
    BrokerOrderRequest,
    BrokerOrderResponse,
    BrokerPositionResponse,
    BrokerTradeResponse,
)
from libs.contracts.models import generate_id, utc_now

logger = logging.getLogger(__name__)


class PaperBrokerAdapter(BrokerAdapter):
    """Simulated broker executing orders in-memory with realistic fills and position tracking."""

    def __init__(self, initial_cash: float = 500_000.0) -> None:
        self.initial_cash = initial_cash
        self.total_cash = initial_cash
        self.used_margin = 0.0
        self._orders: dict[str, dict] = {}
        self._positions: dict[str, dict] = {}
        self._trades: list[BrokerTradeResponse] = []
        self._market_prices: dict[str, float] = {}

    def set_mock_market_price(self, symbol: str, price: float) -> None:
        """Update market price used for fills and unrealized PnL."""
        self._market_prices[symbol] = price

    async def authenticate(self, api_key: str, secret_key: str, session_token: str) -> bool:
        return True

    async def get_funds(self) -> BrokerFunds:
        available = max(0.0, self.total_cash - self.used_margin)
        return BrokerFunds(
            available_margin=available,
            total_cash=self.total_cash,
            used_margin=self.used_margin,
            unrealized_m2m=0.0,
        )

    async def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResponse:
        broker_order_id = f"PAPER-{generate_id()[:8].upper()}"
        normalized_type = request.order_type.lower().replace("-", "_")
        if normalized_type in {"stop_limit", "stoploss"}:
            if request.trigger_price is None or request.trigger_price <= 0:
                return BrokerOrderResponse(
                    success=False,
                    client_order_id=request.client_order_id,
                    status="REJECTED",
                    message="STOP_LIMIT requires positive trigger_price",
                )
            self._orders[broker_order_id] = {
                "broker_order_id": broker_order_id,
                "client_order_id": request.client_order_id,
                "stock_code": request.stock_code,
                "action": request.action,
                "quantity": request.quantity,
                "price": request.price,
                "trigger_price": request.trigger_price,
                "status": "OPEN",
                "filled_quantity": 0,
                "average_price": 0.0,
            }
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_order_id,
                client_order_id=request.client_order_id,
                status="OPEN",
                message="Paper stop-limit accepted and left resting",
            )

        fill_price = request.price
        if request.order_type.lower() == "market":
            fill_price = self._market_prices.get(request.stock_code, request.price or 100.0)

        cost = fill_price * request.quantity
        if request.action.lower() == "buy" and (self.total_cash - self.used_margin) < cost:
            return BrokerOrderResponse(
                success=False,
                client_order_id=request.client_order_id,
                status="REJECTED",
                message="Insufficient paper margin available",
            )

        # Record trade fill
        trade_id = f"TRD-{generate_id()[:8].upper()}"
        now = utc_now()
        trade = BrokerTradeResponse(
            trade_id=trade_id,
            broker_order_id=broker_order_id,
            client_order_id=request.client_order_id,
            stock_code=request.stock_code,
            exchange_code=request.exchange_code,
            action=request.action.upper(),
            quantity=request.quantity,
            price=fill_price,
            trade_time=now,
        )
        self._trades.append(trade)

        # Update position
        symbol = request.stock_code
        pos = self._positions.get(
            symbol,
            {
                "quantity": 0,
                "buy_qty": 0,
                "sell_qty": 0,
                "buy_val": 0.0,
                "sell_val": 0.0,
                "avg_price": 0.0,
            },
        )
        qty_signed = request.quantity if request.action.lower() == "buy" else -request.quantity
        new_qty = pos["quantity"] + qty_signed

        if request.action.lower() == "buy":
            pos["buy_qty"] += request.quantity
            pos["buy_val"] += cost
            self.used_margin += cost
        else:
            pos["sell_qty"] += request.quantity
            pos["sell_val"] += cost
            self.used_margin = max(0.0, self.used_margin - cost)

        if new_qty != 0:
            pos["avg_price"] = pos["buy_val"] / pos["buy_qty"] if pos["buy_qty"] > 0 else fill_price
        else:
            pos["avg_price"] = 0.0

        pos["quantity"] = new_qty
        self._positions[symbol] = pos

        self._orders[broker_order_id] = {
            "broker_order_id": broker_order_id,
            "client_order_id": request.client_order_id,
            "stock_code": request.stock_code,
            "action": request.action,
            "quantity": request.quantity,
            "price": fill_price,
            "status": "FILLED",
            "filled_quantity": request.quantity,
            "average_price": fill_price,
        }

        logger.info(
            "Paper order executed: %s %d %s @ %.2f",
            request.action.upper(),
            request.quantity,
            request.stock_code,
            fill_price,
        )

        return BrokerOrderResponse(
            success=True,
            broker_order_id=broker_order_id,
            client_order_id=request.client_order_id,
            status="FILLED",
            filled_quantity=request.quantity,
            average_price=fill_price,
            message="Paper order filled successfully",
        )

    async def modify_order(
        self,
        broker_order_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
    ) -> BrokerOrderResponse:
        order = self._orders.get(broker_order_id)
        if not order:
            return BrokerOrderResponse(
                success=False,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="REJECTED",
                message="Order not found",
            )
        if order["status"] == "FILLED":
            return BrokerOrderResponse(
                success=False,
                broker_order_id=broker_order_id,
                client_order_id=order["client_order_id"],
                status="REJECTED",
                message="Cannot modify already filled order",
            )
        if quantity is not None:
            order["quantity"] = quantity
        if price is not None:
            order["price"] = price
        return BrokerOrderResponse(
            success=True,
            broker_order_id=broker_order_id,
            client_order_id=order["client_order_id"],
            status="MODIFIED",
            message="Order modified",
        )

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResponse:
        order = self._orders.get(broker_order_id)
        if not order:
            return BrokerOrderResponse(
                success=False,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="REJECTED",
                message="Order not found",
            )
        order["status"] = "CANCELLED"
        return BrokerOrderResponse(
            success=True,
            broker_order_id=broker_order_id,
            client_order_id=order["client_order_id"],
            status="CANCELLED",
            message="Order cancelled",
        )

    async def get_order_status(self, broker_order_id: str) -> Optional[BrokerOrderResponse]:
        order = self._orders.get(broker_order_id)
        if not order:
            return None
        return BrokerOrderResponse(
            success=True,
            broker_order_id=order["broker_order_id"],
            client_order_id=order["client_order_id"],
            status=order["status"],
            filled_quantity=order.get("filled_quantity", 0),
            average_price=order.get("average_price", 0.0),
        )

    async def find_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrderResponse]:
        for order in self._orders.values():
            if order.get("client_order_id") == client_order_id:
                return BrokerOrderResponse(
                    success=order.get("status") not in {"REJECTED", "CANCELLED"},
                    broker_order_id=order.get("broker_order_id"),
                    client_order_id=client_order_id,
                    status=str(order.get("status") or "UNKNOWN"),
                    filled_quantity=int(order.get("filled_quantity") or 0),
                    average_price=float(order.get("average_price") or 0.0),
                )
        return None

    async def get_positions(self) -> list[BrokerPositionResponse]:
        results = []
        for symbol, pos in self._positions.items():
            if pos["quantity"] == 0:
                continue
            ltp = self._market_prices.get(symbol, pos["avg_price"])
            pnl = (ltp - pos["avg_price"]) * pos["quantity"]
            results.append(
                BrokerPositionResponse(
                    stock_code=symbol,
                    exchange_code="NFO",
                    product_type="options",
                    quantity=pos["quantity"],
                    average_price=pos["avg_price"],
                    ltp=ltp,
                    pnl=pnl,
                )
            )
        return results

    async def get_trades(self) -> list[BrokerTradeResponse]:
        return list(self._trades)

