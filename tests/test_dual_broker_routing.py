"""Regression tests for concurrent Breeze/Kite routing and broker-owned execution."""

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from libs.broker_models.adapter import BrokerOrderResponse
from libs.config.settings import (
    AppEnv,
    BrokerBackend,
    EventBusBackend,
    MarketDataBackend,
    PlatformSettings,
)
from libs.contracts.models import (
    BrokerOrder,
    OptionRight,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
    ProductType,
    SourceType,
    TimeInForce,
    TradingMode,
    utc_now,
)
from libs.events.bus import EventEnvelope, InMemoryEventBus, Topics
from services.broker_gateway.service import BrokerGatewayService
from services.execution.service import ExecutionService
from services.oms.repository import OMSRepository
from services.oms.service import OMSService


class _Adapter:
    def __init__(self, name: str, *, active: bool = True) -> None:
        self.name = name
        self.is_active = active

    async def initialize(self) -> None:
        return None


def test_kite_only_production_configuration_does_not_require_breeze(tmp_path: Path):
    settings = PlatformSettings(
        _env_file=None,
        app_env=AppEnv.PRODUCTION,
        data_root=tmp_path,
        event_bus_backend=EventBusBackend.REDPANDA,
        redpanda_brokers="localhost:19092",
        market_data_backend=MarketDataBackend.KITE,
        broker_backend=BrokerBackend.KITE,
        kite_api_key=SecretStr("kite-key"),
        kite_api_secret=SecretStr("kite-secret"),
        auth_signing_key=SecretStr("a-production-signing-key-that-is-long-enough"),
    )
    assert settings.market_data_backend == MarketDataBackend.KITE
    assert settings.breeze_api_key is None


def test_gateway_has_capability_specific_primary_and_fallback(tmp_path: Path):
    settings = PlatformSettings(
        _env_file=None,
        data_root=tmp_path,
        broker_backend=BrokerBackend.KITE,
        live_data_primary=BrokerBackend.KITE,
        live_data_secondary=BrokerBackend.BREEZE,
        historical_primary=BrokerBackend.BREEZE,
        historical_secondary=BrokerBackend.KITE,
        option_chain_primary=BrokerBackend.KITE,
        option_chain_secondary=BrokerBackend.BREEZE,
    )
    breeze = _Adapter("breeze")
    kite = _Adapter("kite")
    gateway = BrokerGatewayService(
        settings=settings,
        breeze_adapter=breeze,  # type: ignore[arg-type]
        kite_adapter=kite,  # type: ignore[arg-type]
    )

    assert gateway.active_adapter is kite
    assert gateway.provider_order("live") == (BrokerBackend.KITE, BrokerBackend.BREEZE)
    assert gateway.provider_order("historical") == (BrokerBackend.BREEZE, BrokerBackend.KITE)
    assert gateway.provider_order("option_chain") == (BrokerBackend.KITE, BrokerBackend.BREEZE)
    assert gateway.get_broker_adapter("breeze") is breeze


@pytest.mark.asyncio
async def test_oms_round_trip_preserves_broker_and_option_identity(tmp_path: Path):
    repo = OMSRepository(db_path=tmp_path / "oms.db")
    await repo.initialize()

    intent = OrderIntent(
        correlation_id="trade-1",
        strategy_instance_id="strategy",
        source=SourceType.STRATEGY,
        instrument_id="INST-NIFTY-2026-09-29-25000-CE",
        symbol="NIFTY26SEP25000CE",
        execution_broker="breeze",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_date="2026-09-29",
        strike_price=25000.0,
        option_right=OptionRight.CALL,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=65,
        price=100.0,
        product=ProductType.OPTIONS,
        time_in_force=TimeInForce.DAY,
        trading_mode=TradingMode.LIVE,
    )
    await repo.save_order_intent(intent, outbox_topic=Topics.ORDER_INTENT)

    order = BrokerOrder(
        intent_id=intent.intent_id,
        client_order_id="CL-1",
        broker_order_id="BR-1",
        instrument_id=intent.instrument_id,
        symbol=intent.symbol,
        execution_broker=intent.execution_broker,
        stock_code=intent.stock_code,
        exchange_code=intent.exchange_code,
        expiry_date=intent.expiry_date,
        strike_price=intent.strike_price,
        option_right=intent.option_right,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        filled_quantity=0,
        remaining_quantity=intent.quantity,
        price=intent.price,
        status=OrderState.OPEN,
        trading_mode=TradingMode.LIVE,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    await repo.save_broker_order_with_transition(
        order,
        from_state=None,
        to_state=OrderState.OPEN,
    )

    loaded = await repo.get_order_by_id(order.order_id)
    assert loaded is not None
    assert loaded.execution_broker == "breeze"
    assert loaded.stock_code == "NIFTY"
    assert loaded.expiry_date == "2026-09-29"
    assert loaded.strike_price == 25000.0
    assert loaded.option_right == OptionRight.CALL


class _ReconGateway:
    active_broker_name = "kite"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def get_order_status(self, broker_order_id, mode, broker):
        self.calls.append((broker, broker_order_id))
        return BrokerOrderResponse(
            success=True,
            broker_order_id=broker_order_id,
            client_order_id="CL-2",
            status="FILLED",
            filled_quantity=65,
            average_price=101.5,
        )


@pytest.mark.asyncio
async def test_reconciliation_uses_order_owning_broker(tmp_path: Path):
    bus = InMemoryEventBus()
    await bus.start()
    repo = OMSRepository(db_path=tmp_path / "oms-reconcile.db")
    oms = OMSService(repository=repo, event_bus=bus)
    await oms.initialize()

    intent = OrderIntent(
        correlation_id="trade-2",
        instrument_id="INST-NIFTY-2026-09-29-25000-CE",
        symbol="NIFTY26SEP25000CE",
        execution_broker="breeze",
        stock_code="NIFTY",
        expiry_date="2026-09-29",
        strike_price=25000.0,
        option_right=OptionRight.CALL,
        side=OrderSide.BUY,
        quantity=65,
        price=101.0,
        trading_mode=TradingMode.LIVE,
    )
    order = await oms.create_order_intent(intent)
    open_order = order.model_copy(
        update={
            "broker_order_id": "BR-2",
            "status": OrderState.OPEN,
        }
    )
    await repo.save_broker_order_with_transition(
        open_order,
        from_state=OrderState.VALIDATING,
        to_state=OrderState.OPEN,
    )

    gateway = _ReconGateway()
    execution = ExecutionService(
        broker_gateway=gateway,  # type: ignore[arg-type]
        oms_service=oms,
        event_bus=bus,
    )
    await execution.reconcile_live_orders()
    await asyncio.sleep(0.05)

    assert gateway.calls == [("breeze", "BR-2")]
    reconciled = await repo.get_order_by_id(order.order_id)
    assert reconciled is not None
    assert reconciled.status == OrderState.FILLED
    assert reconciled.filled_quantity == 65
    assert reconciled.average_price == 101.5
    await bus.stop()


@pytest.mark.asyncio
async def test_oms_accepts_repeated_partial_fill_progress(tmp_path: Path):
    bus = InMemoryEventBus()
    await bus.start()
    repo = OMSRepository(db_path=tmp_path / "oms-partial.db")
    oms = OMSService(repository=repo, event_bus=bus)
    await oms.initialize()

    intent = OrderIntent(
        correlation_id="trade-partial",
        instrument_id="INST-NIFTY-2026-09-29-25000-CE",
        symbol="NIFTY26SEP25000CE",
        execution_broker="kite",
        stock_code="NIFTY26SEP25000CE",
        expiry_date="2026-09-29",
        strike_price=25000.0,
        option_right=OptionRight.CALL,
        side=OrderSide.BUY,
        quantity=65,
        price=100.0,
        trading_mode=TradingMode.LIVE,
    )
    order = await oms.create_order_intent(intent)
    partial = order.model_copy(
        update={
            "broker_order_id": "KITE-PARTIAL",
            "status": OrderState.PARTIALLY_FILLED,
            "filled_quantity": 20,
            "remaining_quantity": 45,
            "average_price": 100.5,
        }
    )
    await repo.save_broker_order_with_transition(
        partial,
        from_state=OrderState.VALIDATING,
        to_state=OrderState.PARTIALLY_FILLED,
    )

    await bus.publish(
        EventEnvelope(
            topic=Topics.BROKER_ORDER_EVENT,
            payload={
                "client_order_id": order.client_order_id,
                "broker_order_id": "KITE-PARTIAL",
                "status": "PARTIALLY_FILLED",
                "filled_quantity": 40,
                "average_price": 100.75,
                "execution_broker": "kite",
            },
        )
    )
    await asyncio.sleep(0.05)

    updated = await repo.get_order_by_id(order.order_id)
    assert updated is not None
    assert updated.status == OrderState.PARTIALLY_FILLED
    assert updated.filled_quantity == 40
    assert updated.remaining_quantity == 25
    assert updated.average_price == 100.75
    await bus.stop()


class _AllowLiveGate:
    def validate_live_order(self, account_id: str):
        return True, "Authorized"


class _SingleOrderOMS:
    def __init__(self, order: BrokerOrder) -> None:
        self.order = order

    async def get_order(self, order_id: str):
        return self.order if order_id == self.order.order_id else None


class _CaptureExecutionGateway:
    active_broker_name = "kite"

    def __init__(self) -> None:
        self.requests = []

    async def place_order(self, request, mode, broker):
        self.requests.append((request, mode, broker))
        return BrokerOrderResponse(
            success=True,
            broker_order_id="BROKER-1",
            client_order_id=request.client_order_id,
            status="PLACED",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broker", "stored_stock_code", "expected_stock_code"),
    [
        ("kite", "NIFTY", "NIFTY26SEP25000CE"),
        ("breeze", "NIFTY", "NIFTY"),
    ],
)
async def test_execution_boundary_maps_option_identifier_per_broker(
    broker: str,
    stored_stock_code: str,
    expected_stock_code: str,
):
    now = utc_now()
    order = BrokerOrder(
        intent_id="intent-broker-map",
        client_order_id="CL-MAP",
        instrument_id="INST-NIFTY-2026-09-29-25000-CE",
        symbol="NIFTY26SEP25000CE",
        execution_broker=broker,
        stock_code=stored_stock_code,
        exchange_code="NFO",
        expiry_date="2026-09-29",
        strike_price=25000.0,
        option_right=OptionRight.CALL,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=65,
        remaining_quantity=65,
        price=100.0,
        status=OrderState.OPEN,
        trading_mode=TradingMode.LIVE,
        created_at=now,
        updated_at=now,
    )
    bus = InMemoryEventBus()
    await bus.start()
    gateway = _CaptureExecutionGateway()
    execution = ExecutionService(
        broker_gateway=gateway,  # type: ignore[arg-type]
        oms_service=_SingleOrderOMS(order),  # type: ignore[arg-type]
        live_gate=_AllowLiveGate(),  # type: ignore[arg-type]
        event_bus=bus,
    )

    await execution.execute_order(order.order_id, order.client_order_id)

    assert len(gateway.requests) == 1
    request, mode, routed_broker = gateway.requests[0]
    assert routed_broker == broker
    assert mode == TradingMode.LIVE
    assert request.stock_code == expected_stock_code
    assert request.expiry_date == "2026-09-29"
    assert request.strike_price == 25000.0
    assert request.right == "call"
    await bus.stop()
