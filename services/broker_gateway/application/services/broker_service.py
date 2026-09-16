"""Broker Application Service.

Clean Architecture Application layer orchestrating ports, adapters, and write guards.
"""

from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Any, Optional

from services.broker_gateway.application.execution_guard import ExecutionGuard
from services.broker_gateway.domain.enums import BrokerWriteAction, Exchange, FeedInterval
from services.broker_gateway.domain.models.account import FundsSnapshot, MarginSnapshot
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef
from services.broker_gateway.domain.models.market_data import (
    Candle,
    OptionChainSnapshot,
    Quote,
)
from services.broker_gateway.domain.models.orders import (
    BrokerOrderAcknowledgement,
    BrokerOrderDetail,
    BrokerOrderRequest,
    CancelBrokerOrderRequest,
    ModifyBrokerOrderRequest,
    SquareOffRequest,
)
from services.broker_gateway.domain.models.positions import BrokerPositionDetail
from services.broker_gateway.domain.models.session import (
    SessionCredentials,
    SessionStatusSnapshot,
)
from services.broker_gateway.domain.models.trades import BrokerTradeDetail
from services.broker_gateway.domain.ports.account_port import BrokerAccountPort
from services.broker_gateway.domain.ports.market_data_port import BrokerMarketDataPort
from services.broker_gateway.domain.ports.request_ledger_port import BrokerRequestLedgerPort
from services.broker_gateway.domain.ports.session_port import BrokerSessionPort
from services.broker_gateway.domain.ports.stream_port import BrokerStreamPort
from services.broker_gateway.domain.ports.trading_port import BrokerTradingPort

logger = logging.getLogger(__name__)


class BrokerApplicationService:
    """Core broker orchestration service implementing platform clean architecture."""

    def __init__(
        self,
        session_port: BrokerSessionPort,
        account_port: BrokerAccountPort,
        market_data_port: BrokerMarketDataPort,
        trading_port: BrokerTradingPort,
        stream_port: Optional[BrokerStreamPort],
        ledger: BrokerRequestLedgerPort,
        execution_guard: Optional[ExecutionGuard] = None,
    ) -> None:
        self.session_port = session_port
        self.account_port = account_port
        self.market_data_port = market_data_port
        self.trading_port = trading_port
        self.stream_port = stream_port
        self.ledger = ledger
        self.guard = execution_guard or ExecutionGuard(ledger=ledger, session_port=session_port)

    # --- Session Management ---
    async def activate_session(self, credentials: SessionCredentials) -> SessionStatusSnapshot:
        return await self.session_port.activate(credentials)

    async def validate_session(self) -> SessionStatusSnapshot:
        return await self.session_port.validate()

    async def disconnect_session(self) -> None:
        await self.session_port.disconnect()

    async def get_session_status(self) -> SessionStatusSnapshot:
        return await self.session_port.get_status()

    # --- Account & Margin Queries ---
    async def get_funds(self) -> FundsSnapshot:
        return await self.account_port.get_funds()

    async def get_margin(self, exchange: Exchange) -> MarginSnapshot:
        return await self.account_port.get_margin(exchange)

    # --- Market Data Queries ---
    async def get_quote(self, instrument: BrokerInstrumentRef) -> Quote:
        return await self.market_data_port.get_quote(instrument)

    async def get_historical(
        self,
        instrument: BrokerInstrumentRef,
        interval: FeedInterval,
        from_date: datetime,
        to_date: datetime,
    ) -> list[Candle]:
        return await self.market_data_port.get_historical(
            instrument=instrument,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )

    async def get_option_chain(
        self,
        underlying: str,
        expiry: date,
        exchange: str = "NFO",
    ) -> OptionChainSnapshot:
        return await self.market_data_port.get_option_chain(
            underlying=underlying,
            expiry=expiry,
            exchange=exchange,
        )

    # --- Guarded Order Writes ---
    async def place_order(self, request: BrokerOrderRequest) -> BrokerOrderAcknowledgement:
        payload = {
            "request_id": request.request_id,
            "account_id": request.account_id,
            "stock_code": request.instrument.stock_code,
            "exchange": request.instrument.exchange.value,
            "side": request.side.value,
            "quantity": request.quantity,
            "order_style": request.order_style.value,
            "limit_price": str(request.limit_price) if request.limit_price is not None else None,
            "stop_price": str(request.stop_price) if request.stop_price is not None else None,
            "validity": request.validity.value,
            "client_reference": request.client_reference,
        }
        return await self.guard.execute_guarded_write(
            request_id=request.request_id,
            account_id=request.account_id,
            client_reference=request.client_reference,
            action=BrokerWriteAction.PLACE,
            payload=payload,
            execute_fn=lambda: self.trading_port.place_order(request),
        )

    async def modify_order(self, request: ModifyBrokerOrderRequest) -> BrokerOrderAcknowledgement:
        payload = {
            "request_id": request.request_id,
            "broker_order_id": request.broker_order_id,
            "quantity": request.quantity,
            "limit_price": str(request.limit_price) if request.limit_price is not None else None,
            "stop_price": str(request.stop_price) if request.stop_price is not None else None,
        }
        return await self.guard.execute_guarded_write(
            request_id=request.request_id,
            account_id="DEFAULT",
            client_reference=request.broker_order_id,
            action=BrokerWriteAction.MODIFY,
            payload=payload,
            execute_fn=lambda: self.trading_port.modify_order(request),
        )

    async def cancel_order(self, request: CancelBrokerOrderRequest) -> BrokerOrderAcknowledgement:
        payload = {
            "request_id": request.request_id,
            "broker_order_id": request.broker_order_id,
        }
        return await self.guard.execute_guarded_write(
            request_id=request.request_id,
            account_id="DEFAULT",
            client_reference=request.broker_order_id,
            action=BrokerWriteAction.CANCEL,
            payload=payload,
            execute_fn=lambda: self.trading_port.cancel_order(request),
        )

    async def square_off(self, request: SquareOffRequest) -> BrokerOrderAcknowledgement:
        payload = {
            "request_id": request.request_id,
            "stock_code": request.instrument.stock_code,
            "quantity": request.quantity,
            "limit_price": str(request.limit_price) if request.limit_price is not None else None,
        }
        return await self.guard.execute_guarded_write(
            request_id=request.request_id,
            account_id="DEFAULT",
            client_reference=request.request_id,
            action=BrokerWriteAction.SQUARE_OFF,
            payload=payload,
            execute_fn=lambda: self.trading_port.square_off(request),
        )

    # --- Reconciliations & Queries ---
    async def get_orders(self) -> list[BrokerOrderDetail]:
        return await self.trading_port.get_orders()

    async def get_order_detail(self, broker_order_id: str) -> Optional[BrokerOrderDetail]:
        return await self.trading_port.get_order_detail(broker_order_id)

    async def get_trades(self) -> list[BrokerTradeDetail]:
        return await self.trading_port.get_trades()

    async def get_positions(self) -> list[BrokerPositionDetail]:
        return await self.trading_port.get_positions()

