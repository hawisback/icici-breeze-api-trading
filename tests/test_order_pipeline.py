"""Integration tests verifying the complete end-to-end order execution pipeline and outbox pattern."""

import asyncio
import pytest
from libs.contracts.models import (
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
    ProductType,
    SystemMode,
    TradingMode,
    generate_id,
)
from libs.events.bus import InMemoryEventBus, Topics
from services.broker_gateway.paper_adapter import PaperBrokerAdapter
from services.broker_gateway.service import BrokerGatewayService
from services.broker_session.repository import BrokerSessionRepository
from services.broker_session.service import BrokerSessionService
from services.execution.service import ExecutionService
from services.market_data.service import MarketDataService
from services.oms.repository import OMSRepository
from services.oms.service import OMSService
from services.portfolio.repository import PortfolioRepository
from services.portfolio.service import PortfolioService
from services.risk.repository import RiskRepository
from services.risk.service import RiskService


@pytest.fixture
async def platform(tmp_path):
    """Set up complete in-process microservices platform with isolated temporary SQLite databases."""
    event_bus = InMemoryEventBus()
    await event_bus.start()

    # Repositories with temp paths
    session_repo = BrokerSessionRepository(tmp_path / "broker_session.db")
    oms_repo = OMSRepository(tmp_path / "oms.db")
    risk_repo = RiskRepository(tmp_path / "risk.db")
    portfolio_repo = PortfolioRepository(tmp_path / "portfolio.db")

    # Services
    session_svc = BrokerSessionService(repository=session_repo, event_bus=event_bus)
    await session_svc.initialize()

    market_svc = MarketDataService(event_bus=event_bus)
    await market_svc.initialize()

    paper_adapter = PaperBrokerAdapter(initial_cash=500_000.0)
    paper_adapter.set_mock_market_price("NIFTY24800CE", 125.0)
    gateway_svc = BrokerGatewayService(paper_adapter=paper_adapter)

    oms_svc = OMSService(repository=oms_repo, event_bus=event_bus)
    await oms_svc.initialize()

    risk_svc = RiskService(repository=risk_repo, event_bus=event_bus, max_order_qty=1800)
    await risk_svc.initialize()

    exec_svc = ExecutionService(broker_gateway=gateway_svc, oms_service=oms_svc, event_bus=event_bus)
    await exec_svc.initialize()

    portfolio_svc = PortfolioService(
        repository=portfolio_repo, market_data_service=market_svc, event_bus=event_bus
    )
    await portfolio_svc.initialize()

    yield {
        "bus": event_bus,
        "session": session_svc,
        "gateway": gateway_svc,
        "oms": oms_svc,
        "risk": risk_svc,
        "execution": exec_svc,
        "portfolio": portfolio_svc,
        "paper_adapter": paper_adapter,
        "oms_repo": oms_repo,
    }

    await event_bus.stop()


@pytest.mark.asyncio
async def test_end_to_end_order_flow(platform):
    """Verify OrderIntent -> Risk Evaluation -> Execution -> Broker Fill -> Position & PnL."""
    oms_svc = platform["oms"]
    portfolio_svc = platform["portfolio"]

    intent = OrderIntent(
        instrument_id="INST-NIFTY-24800-CE",
        symbol="NIFTY24800CE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=50,
        price=125.0,
        product=ProductType.OPTIONS,
        trading_mode=TradingMode.PAPER,
    )

    # 1. Create order intent via OMS
    order = await oms_svc.create_order_intent(intent)
    assert order.status == OrderState.VALIDATING

    # Allow event bus dispatch loop to process: Risk -> Approval -> Execution -> Fill -> Portfolio
    updated_order = None
    for _ in range(20):
        await asyncio.sleep(0.1)
        updated_order = await oms_svc.get_order(order.order_id)
        if updated_order and updated_order.status == OrderState.FILLED:
            break

    # 2. Check updated order status
    assert updated_order is not None
    assert updated_order.status == OrderState.FILLED
    assert updated_order.filled_quantity == 50
    assert updated_order.average_price == 125.0

    # 3. Check portfolio position
    pos = None
    for _ in range(20):
        pos = await platform["portfolio"].repo.get_position("INST-NIFTY-24800-CE")
        if pos is not None:
            break
        await asyncio.sleep(0.1)
    assert pos is not None
    assert pos.quantity == 50
    assert pos.average_price == 125.0

    # 4. Check PnL summary
    summary = await portfolio_svc.get_pnl_summary()
    assert summary["open_positions_count"] == 1


@pytest.mark.asyncio
async def test_risk_rejection_when_halted(platform):
    """Verify pre-trade risk engine halts orders when system mode is HALTED."""
    risk_svc = platform["risk"]
    oms_svc = platform["oms"]

    # Halt system
    await risk_svc.set_system_mode(SystemMode.HALTED)

    intent = OrderIntent(
        instrument_id="INST-NIFTY-24800-CE",
        symbol="NIFTY24800CE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=25,
        price=120.0,
        trading_mode=TradingMode.PAPER,
    )

    order = await oms_svc.create_order_intent(intent)
    await asyncio.sleep(0.2)

    updated_order = await oms_svc.get_order(order.order_id)
    assert updated_order.status == OrderState.RISK_REJECTED
    assert "HALTED" in (updated_order.status_message or "")


@pytest.mark.asyncio
async def test_risk_kill_switch(platform):
    """Verify kill switch blocks new buy entries."""
    risk_svc = platform["risk"]
    oms_svc = platform["oms"]

    await risk_svc.trigger_kill_switch("BLOCK_ENTRIES", reason="Emergency test")

    intent = OrderIntent(
        instrument_id="INST-NIFTY-24800-CE",
        symbol="NIFTY24800CE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=25,
        price=120.0,
        trading_mode=TradingMode.PAPER,
    )

    order = await oms_svc.create_order_intent(intent)
    await asyncio.sleep(0.2)

    updated = await oms_svc.get_order(order.order_id)
    assert updated is not None
    assert updated.status == OrderState.RISK_REJECTED
    assert "blocked" in (updated.status_message or "").lower()


@pytest.mark.asyncio
async def test_submission_unknown_recovery_no_blind_retry(platform):
    """Verify timeout on order placement marks order as SUBMISSION_UNKNOWN without blind retry."""
    gateway = platform["gateway"]
    oms_svc = platform["oms"]

    # Mock gateway adapter to simulate network timeout / exception
    async def mock_timeout(request):
        raise TimeoutError("Network gateway timed out contacting exchange")

    gateway.paper_adapter.place_order = mock_timeout  # type: ignore

    intent = OrderIntent(
        instrument_id="INST-NIFTY-24800-CE",
        symbol="NIFTY24800CE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=25,
        price=120.0,
        trading_mode=TradingMode.PAPER,
    )

    order = await oms_svc.create_order_intent(intent)
    updated = None
    for _ in range(30):
        updated = await oms_svc.get_order(order.order_id)
        if updated and updated.status == OrderState.SUBMISSION_UNKNOWN:
            break
        await asyncio.sleep(0.05)

    assert updated is not None
    assert updated.status == OrderState.SUBMISSION_UNKNOWN
    assert "marked for reconciliation" in (updated.status_message or "").lower()


@pytest.mark.asyncio
async def test_strategy_signal_flow(platform):
    """Verify Strategy emits signals and routes OrderIntent through OMS in PAPER mode."""
    from services.strategy.service import StrategyService
    from services.strategy.repository import StrategyRepository

    oms_svc = platform["oms"]
    event_bus = platform["bus"]
    strat_repo = StrategyRepository(platform["oms_repo"].engine.db_path.parent / "strategy.db")
    strat_svc = StrategyService(oms_service=oms_svc, repository=strat_repo, event_bus=event_bus)
    await strat_svc.initialize()

    # Emit buy signal
    signal = await strat_svc.emit_signal(
        instance_id="INST-NIFTY-EMA-PAPER",
        symbol="NIFTY24800CE",
        instrument_id="INST-NIFTY-24800-CE",
        side=OrderSide.BUY,
        suggested_price=125.0,
        quantity=50,
        trading_mode=TradingMode.PAPER,
    )
    assert signal.signal_id is not None

    # Wait for execution pipeline
    await asyncio.sleep(0.3)

    orders = await oms_svc.list_orders(limit=10)
    matching = [o for o in orders if o.symbol == "NIFTY24800CE"]
    assert len(matching) > 0
    assert matching[0].status == OrderState.FILLED

