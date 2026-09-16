"""Request Mapper translating Domain models into Breeze SDK parameters.

Centralizes all parameter names, enum conversions, date formats, and Decimal conversions.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from services.broker_gateway.domain.enums import (
    Exchange,
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
from services.broker_gateway.infrastructure.icici.datetime_mapper import (
    to_breeze_date_str,
    to_breeze_iso,
)


def map_exchange_to_breeze(exchange: Exchange) -> str:
    return exchange.value


def map_product_to_breeze(product: ProductType) -> str:
    mapping = {
        ProductType.CASH: "cash",
        ProductType.OPTIONS: "options",
        ProductType.FUTURES: "futures",
    }
    return mapping.get(product, "options")


def map_side_to_breeze(side: OrderSide) -> str:
    return "buy" if side == OrderSide.BUY else "sell"


def map_order_style_to_breeze(style: OrderStyle) -> str:
    # Normal market orders are forbidden; LIMIT and STOP_LIMIT only
    mapping = {
        OrderStyle.LIMIT: "limit",
        OrderStyle.STOP_LIMIT: "stoploss",
        OrderStyle.AGGRESSIVE_LIMIT: "limit",
    }
    return mapping.get(style, "limit")


def map_option_right_to_breeze(right: Optional[OptionRight]) -> str:
    if right == OptionRight.CALL:
        return "call"
    if right == OptionRight.PUT:
        return "put"
    return "others"


def map_validity_to_breeze(validity: OrderValidity) -> str:
    return "day" if validity == OrderValidity.DAY else "ioc"


def map_place_order_request(request: BrokerOrderRequest) -> dict[str, Any]:
    """Convert BrokerOrderRequest domain model into Breeze place_order keyword arguments."""
    inst = request.instrument

    expiry_str = ""
    if inst.expiry:
        # Breeze place_order accepts ISO format or date
        expiry_str = f"{to_breeze_date_str(inst.expiry)}T06:00:00.000Z"

    price_str = str(request.limit_price) if request.limit_price is not None else "0"
    stop_str = str(request.stop_price) if request.stop_price is not None else "0"
    strike_str = str(inst.strike) if inst.strike is not None else "0"

    # User remark sanitized (max 20 chars, alphanumeric)
    remark = request.user_remark or request.client_reference[:20]

    return {
        "stock_code": inst.stock_code,
        "exchange_code": map_exchange_to_breeze(inst.exchange),
        "product": map_product_to_breeze(inst.product_type),
        "action": map_side_to_breeze(request.side),
        "order_type": map_order_style_to_breeze(request.order_style),
        "stoploss": stop_str,
        "quantity": str(request.quantity),
        "price": price_str,
        "validity": map_validity_to_breeze(request.validity),
        "validity_date": "",
        "disclosed_quantity": "0",
        "expiry_date": expiry_str,
        "right": map_option_right_to_breeze(inst.option_right),
        "strike_price": strike_str,
        "user_remark": remark,
    }


def map_modify_order_request(
    request: ModifyBrokerOrderRequest,
    exchange_code: str = "NFO",
) -> dict[str, Any]:
    """Convert ModifyBrokerOrderRequest into Breeze modify_order arguments."""
    payload: dict[str, Any] = {
        "order_id": request.broker_order_id,
        "exchange_code": exchange_code,
    }
    if request.quantity is not None:
        payload["quantity"] = str(request.quantity)
    if request.limit_price is not None:
        payload["price"] = str(request.limit_price)
    if request.stop_price is not None:
        payload["stoploss"] = str(request.stop_price)
    return payload


def map_cancel_order_request(
    request: CancelBrokerOrderRequest,
    exchange_code: str = "NFO",
) -> dict[str, Any]:
    """Convert CancelBrokerOrderRequest into Breeze cancel_order arguments."""
    return {
        "order_id": request.broker_order_id,
        "exchange_code": exchange_code,
    }


def map_square_off_request(
    request: SquareOffRequest,
    action: str = "sell",
) -> dict[str, Any]:
    """Convert SquareOffRequest into Breeze square_off arguments."""
    inst = request.instrument
    expiry_str = ""
    if inst.expiry:
        expiry_str = f"{to_breeze_date_str(inst.expiry)}T06:00:00.000Z"

    price_str = str(request.limit_price) if request.limit_price is not None else "0"
    strike_str = str(inst.strike) if inst.strike is not None else "0"

    return {
        "source_flag": "Open",
        "stock_code": inst.stock_code,
        "exchange_code": map_exchange_to_breeze(inst.exchange),
        "quantity": str(request.quantity),
        "price": price_str,
        "action": action,
        "order_type": "limit" if request.limit_price is not None else "market",
        "validity": "day",
        "stoploss": "0",
        "disclosed_quantity": "0",
        "protection_percentage": "",
        "settlement_id": "",
        "margin_flag": "",
        "expiry_date": expiry_str,
        "right": map_option_right_to_breeze(inst.option_right),
        "strike_price": strike_str,
    }

