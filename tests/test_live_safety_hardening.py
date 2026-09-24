"""Regression coverage for live-trading safety hardening."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from libs.broker_models.adapter import BrokerOrderResponse, BrokerPositionResponse
from libs.config.settings import PlatformSettings
from libs.contracts.models import (
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
    SystemMode,
    TradingMode,
)
from libs.events.bus import InMemoryEventBus
from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services
from services.auth.repository import AuthRepository
from services.broker_gateway.service import BrokerGatewayService
from services.execution.service import ExecutionService
from services.oms.repository import OMSRepository
from services.oms.service import OMSService
from services.risk.live_gate import LiveTradingGate
from services.risk.repository import RiskRepository
from services.risk.service import RiskService
from services.strategy.contract_selector import ContractSelector
from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    MarketFeatures,
    OptionType,
    StrategyName,
    TradeDirection,
    TradeLifecycleState,
)
from services.strategy.service import StrategyService


async def _wait_for_order_state(oms: OMSService, order_id: str, state: OrderState):
    latest = None
    for _ in range(50):
        latest = await oms.get_order(order_id)
        if latest and latest.status == state:
            return latest
        await asyncio.sleep(0.02)
    return latest


class _FakeLiveAdapter:
    def __init__(self) -> None:
        self.place_calls = 0
        self.reconcile_response = None

    async def place_order(self, request):
        self.place_calls += 1
        return BrokerOrderResponse(
            success=True,
            broker_order_id="LIVE-ORDER-1",
            client_order_id=request.client_order_id,
            status="PLACED",
        )

    async def get_order_status(self, broker_order_id: str):
        return self.reconcile_response

    async def find_order_by_client_id(self, client_order_id: str):
        return None

    async def get_trades(self):
        return []

    async def get_positions(self):
        return []

    async def get_funds(self):
        raise AssertionError("funds are not used in this test")

    async def modify_order(self, *args, **kwargs):
        raise AssertionError("modify_order is not used in this test")

    async def cancel_order(self, *args, **kwargs):
        raise AssertionError("cancel_order is not used in this test")


@pytest.mark.asyncio
async def test_reduce_only_exit_survives_closed_live_gate_and_reconciles(tmp_path):
    bus = InMemoryEventBus()
    await bus.start()
    settings = PlatformSettings(
        data_root=str(tmp_path),
        live_trading_enabled=False,
        live_allowed_accounts=["ICICI_PRIMARY"],
        broker_backend="breeze",
    )
    gate = LiveTradingGate(settings=settings, event_bus=bus)

    oms = OMSService(
        repository=OMSRepository(tmp_path / "oms.db"),
        event_bus=bus,
    )
    await oms.initialize()
    portfolio = SimpleNamespace(
        repo=SimpleNamespace(
            get_position=AsyncMock(
                return_value=SimpleNamespace(quantity=65)
            )
        )
    )
    risk = RiskService(
        repository=RiskRepository(tmp_path / "risk.db"),
        event_bus=bus,
        live_gate=gate,
        portfolio_service=portfolio,
        broker_gateway=SimpleNamespace(
            active_broker_name="breeze",
            get_positions=AsyncMock(
                return_value=[
                    BrokerPositionResponse(
                        stock_code="NIFTY",
                        exchange_code="NFO",
                        product_type="options",
                        quantity=65,
                        average_price=100.0,
                        ltp=100.0,
                        pnl=0.0,
                        strike_price=25000.0,
                        right="call",
                        expiry_date="2026-09-29",
                    )
                ]
            ),
        ),
    )
    await risk.initialize()

    adapter = _FakeLiveAdapter()
    gateway = BrokerGatewayService(
        breeze_adapter=adapter,  # type: ignore[arg-type]
        settings=settings,
    )
    execution = ExecutionService(
        broker_gateway=gateway,
        oms_service=oms,
        live_gate=gate,
        event_bus=bus,
    )
    await execution.initialize()

    intent = OrderIntent(
        instrument_id="INST-NIFTY-2026-09-29-25000-CE",
        symbol="NIFTY25000CE",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=65,
        price=100.0,
        trading_mode=TradingMode.LIVE,
        reduce_only=True,
    )
    created = await oms.create_order_intent(intent)
    opened = await _wait_for_order_state(oms, created.order_id, OrderState.OPEN)

    assert opened is not None
    assert opened.status == OrderState.OPEN
    assert opened.reduce_only is True
    assert adapter.place_calls == 1
    assert gate.is_live_active() is False

    # Replaying the execution command after the durable state transition must
    # never submit the broker order a second time.
    await execution.execute_order(created.order_id, created.client_order_id)
    assert adapter.place_calls == 1

    adapter.reconcile_response = BrokerOrderResponse(
        success=True,
        broker_order_id="LIVE-ORDER-1",
        client_order_id=created.client_order_id,
        status="COMPLETE",
        filled_quantity=65,
        average_price=98.5,
    )
    await execution.reconcile_live_orders()
    filled = await _wait_for_order_state(oms, created.order_id, OrderState.FILLED)

    assert filled is not None
    assert filled.filled_quantity == 65
    assert filled.average_price == 98.5

    await execution.stop()
    await risk.stop()
    await bus.stop()


@pytest.mark.asyncio
async def test_exit_only_and_halted_allow_only_explicit_reductions(tmp_path):
    bus = InMemoryEventBus()
    await bus.start()
    gate = LiveTradingGate(
        settings=PlatformSettings(data_root=str(tmp_path), live_trading_enabled=False),
        event_bus=bus,
    )
    portfolio = SimpleNamespace(
        repo=SimpleNamespace(
            get_position=AsyncMock(
                return_value=SimpleNamespace(quantity=65)
            )
        )
    )
    risk = RiskService(
        repository=RiskRepository(tmp_path / "risk.db"),
        event_bus=bus,
        live_gate=gate,
        portfolio_service=portfolio,
        broker_gateway=SimpleNamespace(
            get_positions=AsyncMock(
                return_value=[
                    BrokerPositionResponse(
                        stock_code="NIFTYTESTCE",
                        exchange_code="NFO",
                        product_type="options",
                        quantity=65,
                        average_price=100.0,
                        ltp=100.0,
                        pnl=0.0,
                    )
                ]
            )
        ),
    )
    await risk.initialize()

    reduce_exit = OrderIntent(
        instrument_id="INST-NIFTY-TEST-CE",
        symbol="NIFTYTESTCE",
        side=OrderSide.SELL,
        quantity=65,
        price=101.0,
        trading_mode=TradingMode.LIVE,
        reduce_only=True,
    )
    closed_gate_decision = await risk.evaluate_intent(reduce_exit)
    assert closed_gate_decision.approved is True

    invalid_reduce = reduce_exit.model_copy(
        update={
            "intent_id": "INVALID-REDUCE-BUY",
            "side": OrderSide.BUY,
            "price": 102.0,
        }
    )
    invalid_decision = await risk.evaluate_intent(invalid_reduce)
    assert invalid_decision.approved is False
    assert invalid_decision.rule_name == "INVALID_REDUCE_ONLY"

    await risk.set_system_mode(SystemMode.EXIT_ONLY)
    opening_sell = OrderIntent(
        instrument_id="INST-NIFTY-TEST-PE",
        symbol="NIFTYTESTPE",
        side=OrderSide.SELL,
        quantity=65,
        price=103.0,
        trading_mode=TradingMode.PAPER,
    )
    blocked = await risk.evaluate_intent(opening_sell)
    assert blocked.approved is False
    assert blocked.rule_name == "EXIT_ONLY"

    exit_only_reduction = reduce_exit.model_copy(
        update={"intent_id": "EXIT-ONLY-REDUCTION", "price": 104.0}
    )
    allowed = await risk.evaluate_intent(exit_only_reduction)
    assert allowed.approved is True

    await risk.set_system_mode(SystemMode.HALTED)
    halted_reduction = reduce_exit.model_copy(
        update={"intent_id": "HALTED-REDUCTION", "price": 105.0}
    )
    allowed_halted = await risk.evaluate_intent(halted_reduction)
    assert allowed_halted.approved is True

    oversized = reduce_exit.model_copy(
        update={"intent_id": "OVERSIZED-REDUCTION", "quantity": 130}
    )
    oversized_decision = await risk.evaluate_intent(oversized)
    assert oversized_decision.approved is False
    assert oversized_decision.rule_name == "REDUCE_ONLY_QUANTITY_EXCEEDED"

    halted_entry = OrderIntent(
        instrument_id="INST-NIFTY-TEST-CE",
        symbol="NIFTYTESTCE",
        side=OrderSide.BUY,
        quantity=65,
        price=106.0,
        trading_mode=TradingMode.PAPER,
    )
    denied_halted = await risk.evaluate_intent(halted_entry)
    assert denied_halted.approved is False
    assert denied_halted.rule_name == "SYSTEM_HALTED"

    await risk.stop()
    await bus.stop()


@pytest.mark.asyncio
async def test_live_entries_have_independent_notional_position_and_funds_caps(tmp_path):
    bus = InMemoryEventBus()
    await bus.start()
    settings = PlatformSettings(
        data_root=str(tmp_path),
        live_trading_enabled=True,
        live_allowed_accounts=["ICICI_PRIMARY"],
        auth_signing_key="live-safety-test-signing-key-32-bytes-minimum",
        market_data_backend="breeze",
        breeze_api_key="test-live-key",
        breeze_secret_key="test-live-secret",
    )
    gate = LiveTradingGate(settings=settings, event_bus=bus)
    challenge = await gate.request_activation_challenge(
        "OPERATOR",
        "ICICI_PRIMARY",
        30,
    )
    assert await gate.confirm_activation(
        challenge["challenge_id"],
        challenge["challenge_token"],
        "OPERATOR",
    )

    portfolio = SimpleNamespace(
        get_positions=AsyncMock(return_value=[]),
        repo=SimpleNamespace(get_position=AsyncMock(return_value=None)),
    )
    gateway = SimpleNamespace(
        get_funds=AsyncMock(
            return_value=SimpleNamespace(available_margin=100000.0)
        ),
        get_positions=AsyncMock(return_value=[]),
    )
    risk = RiskService(
        repository=RiskRepository(tmp_path / "risk-caps.db"),
        event_bus=bus,
        live_gate=gate,
        portfolio_service=portfolio,
        broker_gateway=gateway,
        broker_session_service=SimpleNamespace(
            get_session_status=AsyncMock(
                return_value={"connected": True, "status": "CONNECTED"}
            )
        ),
        market_data_service=SimpleNamespace(
            get_execution_feed_health=Mock(
                return_value={"healthy": True, "reasons": []}
            )
        ),
        live_max_order_notional=50000.0,
        live_max_open_positions=1,
    )
    await risk.initialize()

    approved = await risk.evaluate_intent(
        OrderIntent(
            intent_id="LIVE-BUY-OK",
            instrument_id="INST-NIFTY-OK-CE",
            symbol="NIFTYOKCE",
            side=OrderSide.BUY,
            quantity=65,
            price=100.0,
            trading_mode=TradingMode.LIVE,
        )
    )
    assert approved.approved is True

    notional = await risk.evaluate_intent(
        OrderIntent(
            intent_id="LIVE-BUY-NOTIONAL",
            instrument_id="INST-NIFTY-BIG-CE",
            symbol="NIFTYBIGCE",
            side=OrderSide.BUY,
            quantity=65,
            price=800.0,
            trading_mode=TradingMode.LIVE,
        )
    )
    assert notional.approved is False
    assert notional.rule_name == "LIVE_ORDER_NOTIONAL_LIMIT"

    naked_sell = await risk.evaluate_intent(
        OrderIntent(
            intent_id="LIVE-NAKED-SELL",
            instrument_id="INST-NIFTY-NAKED-CE",
            symbol="NIFTYNAKEDCE",
            side=OrderSide.SELL,
            quantity=65,
            price=110.0,
            trading_mode=TradingMode.LIVE,
        )
    )
    assert naked_sell.approved is False
    assert naked_sell.rule_name == "LIVE_NAKED_SELL_DISABLED"

    gateway.get_positions.return_value = [
        BrokerPositionResponse(
            stock_code="NIFTYOTHERCE",
            exchange_code="NFO",
            product_type="OPTIONS",
            quantity=65,
            average_price=100.0,
            ltp=100.0,
            pnl=0.0,
        )
    ]
    position_cap = await risk.evaluate_intent(
        OrderIntent(
            intent_id="LIVE-BUY-POSITION-CAP",
            instrument_id="INST-NIFTY-SECOND-CE",
            symbol="NIFTYSECONDCE",
            side=OrderSide.BUY,
            quantity=65,
            price=120.0,
            trading_mode=TradingMode.LIVE,
        )
    )
    assert position_cap.approved is False
    assert position_cap.rule_name == "LIVE_OPEN_POSITION_LIMIT"

    gateway.get_positions.return_value = []
    gateway.get_funds.return_value = SimpleNamespace(available_margin=1000.0)
    funds = await risk.evaluate_intent(
        OrderIntent(
            intent_id="LIVE-BUY-FUNDS",
            instrument_id="INST-NIFTY-FUNDS-CE",
            symbol="NIFTYFUNDSCE",
            side=OrderSide.BUY,
            quantity=65,
            price=130.0,
            trading_mode=TradingMode.LIVE,
        )
    )
    assert funds.approved is False
    assert funds.rule_name == "LIVE_INSUFFICIENT_MARGIN"

    await risk.stop()
    await bus.stop()


def test_contract_selector_preserves_exchange_valid_symbol():
    selector = ContractSelector()
    selected, _, reason = selector.select_contract(
        direction=TradeDirection.BULLISH,
        underlying_price=25000.0,
        option_chain={
            "expiry": "2026-09-29",
            "strikes": [
                {
                    "strike": 25000,
                    "call": {
                        "instrument_id": "INST-NIFTY-2026-09-29-25000-CE",
                        "symbol": "NIFTY26SEP25000CE",
                        "expiry": "2026-09-29",
                        "bid": 49.5,
                        "ask": 50.0,
                        "ltp": 49.8,
                        "open_interest": 50000,
                        "volume": 10000,
                        "lot_size": 65,
                    },
                }
            ],
        },
        as_of=datetime(2026, 9, 24, 4, 0, tzinfo=timezone.utc),
        strategy_a=False,
    )
    assert reason is None
    assert selected is not None
    assert selected.symbol == "NIFTY26SEP25000CE"


def test_execution_builds_exact_breeze_option_identity():
    service = ExecutionService(
        broker_gateway=SimpleNamespace(active_broker_name="breeze"),
        oms_service=SimpleNamespace(),
        live_gate=SimpleNamespace(),
        event_bus=SimpleNamespace(),
    )
    order = SimpleNamespace(
        trading_mode=TradingMode.LIVE,
        instrument_id="INST-NIFTY-2026-09-29-25000-CE",
        symbol="NIFTY26SEP25000CE",
        client_order_id="CL-IDENTITY",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=65,
        price=100.0,
        trigger_price=None,
    )

    request = service._build_broker_request(order)
    assert request is not None
    assert request.stock_code == "NIFTY"
    assert request.expiry_date == "2026-09-29"
    assert request.strike_price == 25000.0
    assert request.right == "call"


@pytest.mark.asyncio
async def test_breeze_reduce_only_requires_exact_expiry_strike_and_right(tmp_path):
    bus = InMemoryEventBus()
    await bus.start()
    broker_gateway = SimpleNamespace(
        active_broker_name="breeze",
        get_positions=AsyncMock(
            return_value=[
                BrokerPositionResponse(
                    stock_code="NIFTY",
                    exchange_code="NFO",
                    product_type="OPTIONS",
                    quantity=65,
                    average_price=100.0,
                    ltp=95.0,
                    pnl=-325.0,
                    strike_price=25100.0,
                    right="call",
                    expiry_date="2026-09-29",
                )
            ]
        ),
    )
    risk = RiskService(
        repository=RiskRepository(tmp_path / "risk-breeze-identity.db"),
        event_bus=bus,
        broker_gateway=broker_gateway,
    )
    await risk.initialize()

    base_intent = OrderIntent(
        intent_id="BREEZE-REDUCE-MISMATCH",
        instrument_id="INST-NIFTY-2026-09-29-25000-CE",
        symbol="NIFTY25000CE",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=65,
        price=90.0,
        trading_mode=TradingMode.LIVE,
        reduce_only=True,
    )
    mismatch = await risk.evaluate_intent(base_intent)
    assert mismatch.approved is False
    assert mismatch.rule_name == "REDUCE_ONLY_QUANTITY_EXCEEDED"

    broker_gateway.get_positions.return_value = [
        BrokerPositionResponse(
            stock_code="NIFTY",
            exchange_code="NFO",
            product_type="OPTIONS",
            quantity=65,
            average_price=100.0,
            ltp=95.0,
            pnl=-325.0,
            strike_price=25000.0,
            right="call",
            expiry_date="2026-09-29",
        )
    ]
    exact = await risk.evaluate_intent(
        base_intent.model_copy(update={"intent_id": "BREEZE-REDUCE-EXACT"})
    )
    assert exact.approved is True

    await risk.stop()
    await bus.stop()


def _live_trade_with_fill() -> ActiveTrade:
    return ActiveTrade(
        trade_id="TRD-LIVE-PROTECT",
        mode=AutoTradingMode.LIVE,
        strategy=StrategyName.VOLATILITY_BREAKOUT,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        contract_symbol="NIFTY26SEP25000CE",
        contract_instrument_id="INST-NIFTY-LIVE-CE",
        expiry="2026-09-29",
        strike=25000.0,
        quantity=65,
        lot_size=65,
        lots=1,
        entry_option_price=100.0,
        entry_spot_price=25000.0,
        initial_structural_stop=24950.0,
        initial_r_points=50.0,
        current_option_price=100.0,
        current_spot_price=25000.0,
        current_trailing_stop=24950.0,
        option_hard_stop_price=75.0,
        state=TradeLifecycleState.OPEN_INITIAL_RISK,
        entry_order_id="ENTRY-1",
        filled_quantity=65,
        initial_quantity=65,
        remaining_quantity=65,
    )


def _strategy_service_for_protection(oms, gateway):
    repo = SimpleNamespace(save_trade=AsyncMock())
    service = StrategyService(
        oms_service=oms,
        repository=repo,
        historical_service=SimpleNamespace(broker_gateway=gateway),
        event_bus=SimpleNamespace(publish=AsyncMock()),
    )
    service._log_decision = AsyncMock()
    return service, repo


@pytest.mark.asyncio
async def test_live_fill_creates_reduce_only_broker_stop_limit():
    oms = SimpleNamespace(
        create_order_intent=AsyncMock(
            return_value=SimpleNamespace(
                order_id="PROTECT-1",
                status=OrderState.VALIDATING,
            )
        ),
        get_order=AsyncMock(),
    )
    gateway = SimpleNamespace(cancel_order=AsyncMock())
    service, repo = _strategy_service_for_protection(oms, gateway)
    trade = _live_trade_with_fill()

    ready = await service._sync_live_protective_stop(
        trade,
        MarketFeatures(spot_price=25000.0),
    )

    assert ready is False
    intent = oms.create_order_intent.await_args.args[0]
    assert intent.side == OrderSide.SELL
    assert intent.order_type == OrderType.STOP_LIMIT
    assert intent.reduce_only is True
    assert intent.quantity == 65
    assert intent.trigger_price == 75.0
    assert intent.price == 67.5
    assert trade.protective_stop_order_id == "PROTECT-1"
    assert trade.protective_stop_status == OrderState.VALIDATING.value
    repo.save_trade.assert_awaited()


@pytest.mark.asyncio
async def test_discretionary_exit_cancels_protection_before_second_sell():
    protective_order = SimpleNamespace(
        status=OrderState.OPEN,
        filled_quantity=0,
        average_price=0.0,
        broker_order_id="BROKER-PROTECT-1",
        trading_mode=TradingMode.LIVE,
    )
    oms = SimpleNamespace(
        create_order_intent=AsyncMock(),
        get_order=AsyncMock(return_value=protective_order),
    )
    gateway = SimpleNamespace(
        cancel_order=AsyncMock(
            return_value=SimpleNamespace(success=True, status="CANCELLED")
        )
    )
    service, _ = _strategy_service_for_protection(oms, gateway)
    trade = _live_trade_with_fill()
    trade.protective_stop_order_id = "PROTECT-1"
    trade.protective_stop_status = "OPEN"

    may_exit = await service._cancel_live_protective_stop_for_exit(
        trade,
        MarketFeatures(spot_price=25000.0),
    )

    assert may_exit is False
    gateway.cancel_order.assert_awaited_once_with(
        "BROKER-PROTECT-1",
        mode=TradingMode.LIVE,
    )
    assert trade.protective_stop_cancel_for_exit is True
    assert trade.protective_stop_status == "CANCEL_REQUESTED"

    protective_order.status = OrderState.CANCELLED
    may_exit_after_cancel = await service._cancel_live_protective_stop_for_exit(
        trade,
        MarketFeatures(spot_price=25000.0),
    )
    assert may_exit_after_cancel is True
    assert trade.protective_stop_order_id is None


@pytest.mark.asyncio
async def test_filled_broker_protection_closes_trade_as_emergency_stop():
    protective_order = SimpleNamespace(
        status=OrderState.FILLED,
        filled_quantity=65,
        average_price=72.0,
        broker_order_id="BROKER-PROTECT-1",
        trading_mode=TradingMode.LIVE,
    )
    oms = SimpleNamespace(
        create_order_intent=AsyncMock(),
        get_order=AsyncMock(return_value=protective_order),
    )
    service, _ = _strategy_service_for_protection(
        oms,
        SimpleNamespace(cancel_order=AsyncMock()),
    )
    service._close_trade = AsyncMock()
    trade = _live_trade_with_fill()
    trade.protective_stop_order_id = "PROTECT-1"

    ready = await service._sync_live_protective_stop(
        trade,
        MarketFeatures(spot_price=25000.0),
    )

    assert ready is False
    assert trade.exit_filled_quantity == 65
    assert trade.exit_proceeds == 72.0 * 65
    service._close_trade.assert_awaited_once()
    assert service._close_trade.await_args.args[3] == "OPTION_EMERGENCY_STOP"


@pytest.mark.asyncio
async def test_reduce_only_stop_limit_geometry_is_fail_closed(tmp_path):
    bus = InMemoryEventBus()
    await bus.start()
    portfolio = SimpleNamespace(
        repo=SimpleNamespace(
            get_position=AsyncMock(return_value=SimpleNamespace(quantity=65))
        )
    )
    risk = RiskService(
        repository=RiskRepository(tmp_path / "risk-stop.db"),
        event_bus=bus,
        portfolio_service=portfolio,
        broker_gateway=SimpleNamespace(
            get_positions=AsyncMock(
                return_value=[
                    BrokerPositionResponse(
                        stock_code="NIFTYLIVECE",
                        exchange_code="NFO",
                        product_type="options",
                        quantity=65,
                        average_price=100.0,
                        ltp=100.0,
                        pnl=0.0,
                    )
                ]
            )
        ),
    )
    await risk.initialize()

    invalid = await risk.evaluate_intent(
        OrderIntent(
            intent_id="BAD-PROTECTIVE-STOP",
            instrument_id="INST-NIFTY-LIVE-CE",
            symbol="NIFTYLIVECE",
            side=OrderSide.SELL,
            order_type=OrderType.STOP_LIMIT,
            quantity=65,
            price=80.0,
            trigger_price=75.0,
            trading_mode=TradingMode.LIVE,
            reduce_only=True,
        )
    )
    assert invalid.approved is False
    assert invalid.rule_name == "STOP_LIMIT_PRICE_INVALID"

    await risk.stop()
    await bus.stop()


@pytest.mark.asyncio
async def test_strategy_kill_switch_still_manages_existing_positions():
    class _Oms:
        pass

    service = StrategyService(oms_service=_Oms())  # type: ignore[arg-type]
    service.config.kill_switch = True
    fake_trade = object()
    managed = []

    async def gather():
        return MarketFeatures(spot_price=24000.0)

    async def get_active_trades():
        return [fake_trade]

    async def manage(trade, features):
        managed.append((trade, features.spot_price))

    async def no_save():
        return None

    async def observe(**kwargs):
        return {"status": "DISABLED"}

    service._gather_features = gather  # type: ignore[method-assign]
    service.repo.get_active_trades = get_active_trades  # type: ignore[method-assign]
    service._evaluate_active_trade = manage  # type: ignore[method-assign]
    service._save_runtime = no_save  # type: ignore[method-assign]
    service.strategy_c_shadow.observe = observe  # type: ignore[method-assign]
    service.strategy_d_paper.observe = observe  # type: ignore[method-assign]

    result = await service._evaluate_cycle()

    assert result["status"] == "HALTED_KILL_SWITCH"
    assert result["active_positions_managed"] == 1
    assert managed == [(fake_trade, 24000.0)]


@pytest.mark.asyncio
async def test_internal_http_broker_writes_are_not_a_bypass(tmp_path):
    settings = PlatformSettings(data_root=str(tmp_path), live_trading_enabled=False)
    container = await initialize_services(settings=settings, force_reinit=True)
    transport = httpx.ASGITransport(app=app)

    payload = {
        "request_id": "REQ-DIRECT-BYPASS",
        "account_id": "ICICI_PRIMARY",
        "instrument": {
            "exchange": "NFO",
            "stock_code": "NIFTY",
            "product_type": "options",
        },
        "side": "BUY",
        "quantity": 65,
        "order_style": "LIMIT",
        "limit_price": "100.00",
        "client_reference": "DIRECT-BYPASS",
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauth = await client.post("/internal/v1/orders", json=payload)
        assert unauth.status_code == 401

        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "operator", "password": "Operator@Trading123!"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        blocked = await client.post(
            "/internal/v1/orders",
            json=payload,
            headers=headers,
        )
        assert blocked.status_code == 403
        assert "Direct broker writes are disabled" in blocked.json()["detail"]

    await container.strategy_svc.stop()
    await container.exec_svc.stop()
    await container.market_svc.stop_simulated_feed()
    await container.oms_svc.stop_outbox_worker()
    await container.event_bus.stop()


def test_live_capable_settings_require_strong_signing_key(tmp_path):
    with pytest.raises(ValueError, match="AUTH_SIGNING_KEY must be at least 32 bytes"):
        PlatformSettings(
            data_root=str(tmp_path),
            live_trading_enabled=True,
            live_allowed_accounts=["ICICI_PRIMARY"],
            auth_signing_key="too-short",
            market_data_backend="breeze",
            breeze_api_key="live-test-key",
            breeze_secret_key="live-test-secret",
        )


@pytest.mark.asyncio
async def test_live_preflight_is_authenticated_and_fail_closed(tmp_path):
    settings = PlatformSettings(
        data_root=str(tmp_path),
        live_trading_enabled=False,
    )
    container = await initialize_services(
        settings=settings,
        force_reinit=True,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        unauth = await client.get("/api/v1/live-preflight")
        assert unauth.status_code == 401

        login = await client.post(
            "/api/v1/auth/login",
            json={
                "username": "operator",
                "password": "Operator@Trading123!",
            },
        )
        assert login.status_code == 200
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}"
        }

        report = await client.get(
            "/api/v1/live-preflight",
            headers=headers,
        )
        assert report.status_code == 200
        data = report.json()
        assert data["readiness"] == "BLOCKED"
        assert "LIVE_TRADING_ENABLED_FALSE" in data["blockers"]
        assert data["event_bus"]["runtime"] == "memory"
        assert data["protective_stop"]["order_type"] == "STOP_LIMIT"

    await container.strategy_svc.stop()
    await container.exec_svc.stop()
    await container.risk_svc.stop()
    await container.market_svc.stop_simulated_feed()
    await container.oms_svc.stop_outbox_worker()
    await container.event_bus.stop()


@pytest.mark.asyncio
async def test_production_auth_db_refuses_predictable_bootstrap_users(tmp_path):
    settings = PlatformSettings(
        app_env="production",
        data_root=str(tmp_path),
        event_bus_backend="redpanda",
        redpanda_brokers="localhost:19092",
        market_data_backend="breeze",
        breeze_api_key="prod-key",
        breeze_secret_key="prod-secret",
        auth_signing_key="a-production-signing-key-that-is-not-a-default",
    )
    repo = AuthRepository(
        db_path=tmp_path / "auth.db",
        settings=settings,
    )

    with pytest.raises(RuntimeError, match="Predictable bootstrap credentials are disabled"):
        await repo.initialize()


@pytest.mark.asyncio
async def test_live_enabled_rejects_existing_known_bootstrap_passwords(tmp_path):
    dev_settings = PlatformSettings(
        data_root=str(tmp_path),
        live_trading_enabled=False,
    )
    dev_repo = AuthRepository(
        db_path=tmp_path / "auth-existing.db",
        settings=dev_settings,
    )
    await dev_repo.initialize()

    live_settings = PlatformSettings(
        data_root=str(tmp_path),
        live_trading_enabled=True,
        live_allowed_accounts=["ICICI_PRIMARY"],
        auth_signing_key="live-test-signing-key-that-is-not-default",
        market_data_backend="breeze",
        breeze_api_key="live-test-key",
        breeze_secret_key="live-test-secret",
    )
    live_repo = AuthRepository(
        db_path=tmp_path / "auth-existing.db",
        settings=live_settings,
    )
    with pytest.raises(
        RuntimeError,
        match="known development bootstrap passwords",
    ):
        await live_repo.initialize()
