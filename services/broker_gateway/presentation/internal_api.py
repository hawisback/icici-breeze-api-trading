"""Broker Gateway Internal HTTP API (/internal/v1).

Strictly internal-only endpoints for inter-service communication (Execution Service, OMS, Market Data).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import logging
from typing import Any, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, SecretStr

from libs.contracts.models import UserRole
from services.api_gateway.dependencies import require_roles
from services.broker_gateway.application.services.broker_service import BrokerApplicationService
from services.broker_gateway.domain.enums import (
    Exchange,
    FeedInterval,
    OptionRight,
    OrderSide,
    OrderStyle,
    OrderValidity,
    ProductType,
)
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef
from services.broker_gateway.domain.models.orders import (
    BrokerOrderRequest,
    CancelBrokerOrderRequest,
    ModifyBrokerOrderRequest,
    SquareOffRequest,
)
from services.broker_gateway.domain.models.session import SessionCredentials

logger = logging.getLogger(__name__)

internal_router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal Broker Gateway"],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR))],
)


def reject_direct_broker_write() -> None:
    """HTTP callers may not bypass OMS/Risk/Execution for broker writes."""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Direct broker writes are disabled on the API gateway. Use the guarded /api/v1 order pipeline.",
    )


# --- Schemas ---
class SessionActivateRequest(BaseModel):
    api_key: str = Field(..., min_length=1)
    secret_key: str = Field(..., min_length=1)
    session_token: str = Field(..., min_length=1)


class InstrumentRefSchema(BaseModel):
    internal_instrument_id: Optional[uuid.UUID] = None
    exchange: Exchange = Exchange.NFO
    stock_code: str
    product_type: ProductType = ProductType.OPTIONS
    expiry: Optional[date] = None
    strike: Optional[Decimal] = None
    option_right: Optional[OptionRight] = None
    stock_token: Optional[str] = None


class PlaceOrderSchema(BaseModel):
    request_id: str
    account_id: str
    instrument: InstrumentRefSchema
    side: OrderSide
    quantity: int
    order_style: OrderStyle = OrderStyle.LIMIT
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    validity: OrderValidity = OrderValidity.DAY
    client_reference: str
    user_remark: Optional[str] = None


class ModifyOrderSchema(BaseModel):
    request_id: str
    quantity: Optional[int] = None
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None


class SquareOffSchema(BaseModel):
    request_id: str
    instrument: InstrumentRefSchema
    quantity: int
    limit_price: Optional[Decimal] = None


class HistoricalQuerySchema(BaseModel):
    instrument: InstrumentRefSchema
    interval: FeedInterval = FeedInterval.ONE_MINUTE
    from_date: datetime
    to_date: datetime


class OptionChainQuerySchema(BaseModel):
    underlying: str
    expiry: date
    exchange: str = "NFO"


# Dependency hook to provide BrokerApplicationService
_clean_service_instance: Optional[BrokerApplicationService] = None


def set_clean_broker_service(service: BrokerApplicationService) -> None:
    global _clean_service_instance
    _clean_service_instance = service


def get_clean_broker_service() -> BrokerApplicationService:
    if _clean_service_instance is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BrokerApplicationService is not initialized",
        )
    return _clean_service_instance


# --- Session Routes ---
@internal_router.post("/session/activate")
async def activate_session(
    body: SessionActivateRequest,
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    creds = SessionCredentials(
        api_key=body.api_key,
        secret_key=SecretStr(body.secret_key),
        session_token=SecretStr(body.session_token),
    )
    snap = await service.activate_session(creds)
    return {
        "status": snap.status.value,
        "account_id": snap.account_id,
        "login_time": snap.login_time.isoformat() if snap.login_time else None,
        "expires_at": snap.expires_at.isoformat() if snap.expires_at else None,
        "message": snap.message,
    }


@internal_router.get("/session/status")
async def get_session_status(
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    snap = await service.get_session_status()
    return {
        "status": snap.status.value,
        "account_id": snap.account_id,
        "login_time": snap.login_time.isoformat() if snap.login_time else None,
        "expires_at": snap.expires_at.isoformat() if snap.expires_at else None,
        "message": snap.message,
    }


@internal_router.post("/session/validate")
async def validate_session(
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    snap = await service.validate_session()
    return {
        "status": snap.status.value,
        "account_id": snap.account_id,
        "message": snap.message,
    }


@internal_router.post("/session/disconnect")
async def disconnect_session(
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, str]:
    await service.disconnect_session()
    return {"status": "DISCONNECTED"}


# --- Account Routes ---
@internal_router.get("/account/funds")
async def get_funds(
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    funds = await service.get_funds()
    return {
        "available_margin": str(funds.available_margin),
        "total_cash": str(funds.total_cash),
        "used_margin": str(funds.used_margin),
        "timestamp": funds.timestamp.isoformat(),
    }


@internal_router.get("/account/margin")
async def get_margin(
    exchange: Exchange = Query(Exchange.NFO),
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    margin = await service.get_margin(exchange)
    return {
        "exchange": margin.exchange.value,
        "total_margin": str(margin.total_margin),
        "required_margin": str(margin.required_margin),
        "available_margin": str(margin.available_margin),
        "timestamp": margin.timestamp.isoformat(),
    }


# --- Market Data Routes ---
@internal_router.post("/quotes")
async def get_quote(
    body: InstrumentRefSchema,
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    ref = BrokerInstrumentRef(
        internal_instrument_id=body.internal_instrument_id or uuid.uuid4(),
        exchange=body.exchange,
        stock_code=body.stock_code,
        product_type=body.product_type,
        expiry=body.expiry,
        strike=body.strike,
        option_right=body.option_right,
        stock_token=body.stock_token,
    )
    quote = await service.get_quote(ref)
    return {
        "stock_code": quote.instrument.stock_code,
        "ltp": str(quote.ltp),
        "best_bid_price": str(quote.best_bid_price) if quote.best_bid_price else None,
        "best_ask_price": str(quote.best_ask_price) if quote.best_ask_price else None,
        "volume": quote.volume,
        "open_interest": quote.open_interest,
        "timestamp": quote.timestamp.isoformat(),
    }


@internal_router.post("/historical")
async def get_historical(
    body: HistoricalQuerySchema,
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> list[dict[str, Any]]:
    ref = BrokerInstrumentRef(
        internal_instrument_id=body.instrument.internal_instrument_id or uuid.uuid4(),
        exchange=body.instrument.exchange,
        stock_code=body.instrument.stock_code,
        product_type=body.instrument.product_type,
        expiry=body.instrument.expiry,
        strike=body.instrument.strike,
        option_right=body.instrument.option_right,
        stock_token=body.instrument.stock_token,
    )
    candles = await service.get_historical(
        instrument=ref,
        interval=body.interval,
        from_date=body.from_date,
        to_date=body.to_date,
    )
    return [
        {
            "start_time": c.start_time.isoformat(),
            "open": str(c.open),
            "high": str(c.high),
            "low": str(c.low),
            "close": str(c.close),
            "volume": c.volume,
            "open_interest": c.open_interest,
        }
        for c in candles
    ]


@internal_router.post("/option-chain")
async def get_option_chain(
    body: OptionChainQuerySchema,
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    chain = await service.get_option_chain(
        underlying=body.underlying,
        expiry=body.expiry,
        exchange=body.exchange,
    )
    return {
        "underlying": chain.underlying,
        "expiry": chain.expiry.isoformat(),
        "spot_price": str(chain.spot_price),
        "timestamp": chain.timestamp.isoformat(),
        "contracts": [
            {
                "strike_price": str(c.strike_price),
                "right": c.right.value,
                "ltp": str(c.ltp),
                "bid": str(c.bid) if c.bid else None,
                "ask": str(c.ask) if c.ask else None,
                "volume": c.volume,
                "open_interest": c.open_interest,
                "oi_change": c.oi_change,
            }
            for c in chain.contracts
        ],
    }


# --- Orders Routes ---
@internal_router.get("/orders")
async def get_orders(
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> list[dict[str, Any]]:
    orders = await service.get_orders()
    return [
        {
            "broker_order_id": o.broker_order_id,
            "client_reference": o.client_reference,
            "stock_code": o.instrument.stock_code,
            "side": o.side.value,
            "quantity": o.quantity,
            "filled_quantity": o.filled_quantity,
            "price": str(o.price),
            "average_price": str(o.average_price),
            "raw_status": o.raw_status,
            "normalized_status": o.normalized_status,
            "order_time": o.order_time.isoformat(),
        }
        for o in orders
    ]


@internal_router.get("/orders/{broker_order_id}")
async def get_order_detail(
    broker_order_id: str,
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    order = await service.get_order_detail(broker_order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "broker_order_id": order.broker_order_id,
        "client_reference": order.client_reference,
        "stock_code": order.instrument.stock_code,
        "side": order.side.value,
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "price": str(order.price),
        "average_price": str(order.average_price),
        "raw_status": order.raw_status,
        "normalized_status": order.normalized_status,
        "order_time": order.order_time.isoformat(),
    }


@internal_router.post("/orders", dependencies=[Depends(reject_direct_broker_write)])
async def place_order(
    body: PlaceOrderSchema,
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    ref = BrokerInstrumentRef(
        internal_instrument_id=body.instrument.internal_instrument_id or uuid.uuid4(),
        exchange=body.instrument.exchange,
        stock_code=body.instrument.stock_code,
        product_type=body.instrument.product_type,
        expiry=body.instrument.expiry,
        strike=body.instrument.strike,
        option_right=body.instrument.option_right,
        stock_token=body.instrument.stock_token,
    )
    req = BrokerOrderRequest(
        request_id=body.request_id,
        account_id=body.account_id,
        instrument=ref,
        side=body.side,
        quantity=body.quantity,
        order_style=body.order_style,
        limit_price=body.limit_price,
        stop_price=body.stop_price,
        validity=body.validity,
        client_reference=body.client_reference,
        user_remark=body.user_remark,
    )
    ack = await service.place_order(req)
    return {
        "request_id": ack.request_id,
        "client_reference": ack.client_reference,
        "broker_order_id": ack.broker_order_id,
        "status": ack.status.value,
        "message": ack.message,
        "acknowledged_at": ack.acknowledged_at.isoformat(),
    }


@internal_router.post("/orders/{broker_order_id}/modify", dependencies=[Depends(reject_direct_broker_write)])
async def modify_order(
    broker_order_id: str,
    body: ModifyOrderSchema,
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    req = ModifyBrokerOrderRequest(
        request_id=body.request_id,
        broker_order_id=broker_order_id,
        quantity=body.quantity,
        limit_price=body.limit_price,
        stop_price=body.stop_price,
    )
    ack = await service.modify_order(req)
    return {
        "request_id": ack.request_id,
        "broker_order_id": ack.broker_order_id,
        "status": ack.status.value,
        "message": ack.message,
    }


@internal_router.post("/orders/{broker_order_id}/cancel", dependencies=[Depends(reject_direct_broker_write)])
async def cancel_order(
    broker_order_id: str,
    request_id: str = Query(...),
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    req = CancelBrokerOrderRequest(
        request_id=request_id,
        broker_order_id=broker_order_id,
    )
    ack = await service.cancel_order(req)
    return {
        "request_id": ack.request_id,
        "broker_order_id": ack.broker_order_id,
        "status": ack.status.value,
        "message": ack.message,
    }


@internal_router.post("/positions/square-off", dependencies=[Depends(reject_direct_broker_write)])
async def square_off(
    body: SquareOffSchema,
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> dict[str, Any]:
    ref = BrokerInstrumentRef(
        internal_instrument_id=body.instrument.internal_instrument_id or uuid.uuid4(),
        exchange=body.instrument.exchange,
        stock_code=body.instrument.stock_code,
        product_type=body.instrument.product_type,
        expiry=body.instrument.expiry,
        strike=body.instrument.strike,
        option_right=body.instrument.option_right,
        stock_token=body.instrument.stock_token,
    )
    req = SquareOffRequest(
        request_id=body.request_id,
        instrument=ref,
        quantity=body.quantity,
        limit_price=body.limit_price,
    )
    ack = await service.square_off(req)
    return {
        "request_id": ack.request_id,
        "broker_order_id": ack.broker_order_id,
        "status": ack.status.value,
        "message": ack.message,
    }


# --- Trades & Positions Routes ---
@internal_router.get("/trades")
async def get_trades(
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> list[dict[str, Any]]:
    trades = await service.get_trades()
    return [
        {
            "trade_id": t.trade_id,
            "broker_order_id": t.broker_order_id,
            "stock_code": t.instrument.stock_code,
            "side": t.side.value,
            "quantity": t.quantity,
            "execution_price": str(t.execution_price),
            "trade_time": t.trade_time.isoformat(),
        }
        for t in trades
    ]


@internal_router.get("/positions")
async def get_positions(
    service: BrokerApplicationService = Depends(get_clean_broker_service),
) -> list[dict[str, Any]]:
    positions = await service.get_positions()
    return [
        {
            "stock_code": p.instrument.stock_code,
            "quantity": p.quantity,
            "average_price": str(p.average_price),
            "ltp": str(p.ltp),
            "realized_pnl": str(p.realized_pnl),
            "unrealized_pnl": str(p.unrealized_pnl),
            "total_pnl": str(p.total_pnl),
        }
        for p in positions
    ]

