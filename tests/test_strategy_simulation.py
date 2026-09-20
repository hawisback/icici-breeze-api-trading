from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from libs.contracts.models import Candle
from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services
from services.historical.repository import HistoricalRepository
from services.strategy.models import (
    SimulatedTradeRecord,
    SimulationRequest,
    SimulationResult,
    RiskConfig,
    ThresholdOverrides,
)
from services.strategy.replay_lifecycle import (
    attach_historical_option_prices,
    build_simulated_trade_records,
    summarize_simulated_pnl,
)
from services.strategy.replay_manifest import ReplayManifestRecord
from services.strategy.simulation import SimulationEngine


def _resolved_manifest(index: int, realized_r: float) -> ReplayManifestRecord:
    entry_time = datetime(2026, 9, 17, 9, 30 + index, tzinfo=timezone.utc)
    return ReplayManifestRecord(
        replay_signal_id=f"SIG-{index}",
        strategy_id="TREND_PULLBACK",
        direction="CALL" if index % 2 == 0 else "PUT",
        trading_date="2026-09-17",
        trigger_source_candle_timestamp=entry_time,
        trigger_level=100.0,
        simulated_entry_timestamp=entry_time,
        simulated_entry_price=100.0,
        entry_5m_candle_timestamp=entry_time,
        entry_occurred_intrabar=True,
        atr_at_entry=1.0,
        initial_structural_stop=99.0,
        initial_risk_points=1.0,
        initial_risk_atr=1.0,
        current_trailing_stop=99.0,
        current_r=0.0,
        peak_r=max(realized_r, 0.0),
        protected_breakeven_active=False,
        profit_lock_active=False,
        runner_mode_active=False,
        current_ladder_stage="OPEN_INITIAL_RISK",
        reversal_score=0,
        entry_bar_timestamp=entry_time,
        lifecycle_status="RESOLVED",
        exit_timestamp=entry_time + timedelta(minutes=15),
        exit_price=100.0 + realized_r,
        exit_reason="SESSION_EXIT",
        realized_r=realized_r,
    )


def test_simulation_result_trades_are_the_canonical_summary_rows():
    """Resolved lifecycle records populate the same rows the UI renders."""
    records = [_resolved_manifest(1, 0.2), _resolved_manifest(2, 0.1), _resolved_manifest(3, -0.1611)]
    trades = build_simulated_trade_records(records)
    result = SimulationResult(
        session_date="2026-09-17",
        total_bars_evaluated=75,
        total_trades=len(trades),
        winning_trades=sum(t.realized_r > 0 for t in trades),
        losing_trades=sum(t.realized_r < 0 for t in trades),
        win_rate_pct=66.67,
        total_pnl=None,
        net_pnl=None,
        total_realized_r=sum(t.realized_r for t in trades),
        max_drawdown_pnl=None,
        profit_factor=1.86,
        trades=trades,
    )

    assert result.total_trades == 3 == len(result.trades)
    assert result.winning_trades == 2
    assert result.losing_trades == 1
    assert all(trade.entry_premium is None for trade in result.trades)
    assert all(trade.net_pnl is None for trade in result.trades)
    assert summarize_simulated_pnl(result.trades) == (None, None)
    serialized = result.model_dump(mode="json")
    assert serialized["trades"][0]["realized_r"] == 0.2
    assert serialized["trades"][0]["entry_premium"] is None
    assert serialized["trades"][0]["net_pnl"] is None
    assert serialized["net_pnl"] is None

    zero_pnl = result.trades[0].model_copy(update={"gross_pnl": 0.0, "net_pnl": 0.0})
    calculated_pnl = result.trades[1].model_copy(update={"gross_pnl": 1250.5, "net_pnl": 1250.5})
    assert summarize_simulated_pnl([zero_pnl, calculated_pnl]) == (1250.5, 1250.5)
    assert summarize_simulated_pnl([zero_pnl, result.trades[2]]) == (None, None)

    assert SimulatedTradeRecord.model_validate(
        {**zero_pnl.model_dump(), "realized_r": 3.2139}
    ).net_pnl == 0.0

    empty_result = SimulationResult(
        session_date="2026-09-17",
        total_bars_evaluated=75,
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate_pct=0.0,
        total_pnl=0.0,
        net_pnl=0.0,
        total_realized_r=0.0,
        max_drawdown_pnl=0.0,
        profit_factor=0.0,
        trades=build_simulated_trade_records([]),
    )
    assert empty_result.total_trades == 0 == len(empty_result.trades)


def test_historical_option_candles_populate_net_pnl():
    record = _resolved_manifest(2, -1.0)
    record.simulated_entry_price = 23306.0
    contract = SimpleNamespace(
        instrument_id="INST-NIFTY-2026-09-22-23300-CE",
        stock_code="NIFTY23300CE",
        expiry="2026-09-22",
        strike=23300.0,
        option_right=SimpleNamespace(value="CALL"),
        lot_size=25,
    )
    candles = [
        Candle(
            instrument_id=contract.instrument_id, interval="1m",
            start_time=record.simulated_entry_timestamp,
            end_time=record.simulated_entry_timestamp + timedelta(minutes=1),
            open=120.0, high=121.0, low=119.0, close=120.0, volume=100,
            source="BREEZE",
        ),
        Candle(
            instrument_id=contract.instrument_id, interval="1m",
            start_time=record.exit_timestamp,
            end_time=record.exit_timestamp + timedelta(minutes=1),
            open=110.0, high=111.0, low=109.0, close=110.0, volume=100,
            source="BREEZE",
        ),
    ]

    attach_historical_option_prices([record], [contract], {contract.instrument_id: candles}, RiskConfig())
    trade = build_simulated_trade_records([record])[0]

    assert record.option_data_status == "AVAILABLE"
    assert trade.entry_premium == 120.0
    assert trade.exit_premium == 110.0
    assert trade.quantity == 25
    assert trade.gross_pnl == -250.0
    assert trade.net_pnl is not None and trade.net_pnl < trade.gross_pnl


@pytest.mark.asyncio
async def test_available_simulation_dates_returns_ordered_historical_sessions(tmp_path):
    repository = HistoricalRepository(tmp_path / "historical.db")
    await repository.initialize()
    candles = []
    for index, day in enumerate((15, 16, 17)):
        start = datetime(2026, 9, day, 9, 15, tzinfo=timezone.utc)
        candles.append(
            Candle(
                instrument_id="INST-NIFTY-INDEX",
                interval="5m",
                start_time=start,
                end_time=start + timedelta(minutes=5),
                open=100.0 + index,
                high=101.0 + index,
                low=99.0 + index,
                close=100.5 + index,
                volume=1000,
                source="BREEZE",
            )
        )
    await repository.save_candles(candles)

    engine = SimulationEngine(historical_service=SimpleNamespace(repo=repository))
    assert await engine.get_available_dates() == ["2026-09-17", "2026-09-16", "2026-09-15"]


def test_simulation_engine_missing_data():
    """Missing history must never be replaced by a fabricated trading session."""
    engine = SimulationEngine()
    req = SimulationRequest(
        date="2026-09-17",
        instrument_id="INST-NIFTY-INDEX",
        bypass_window=True,
    )

    import asyncio
    result = asyncio.run(engine.run_day_simulation(req))

    assert result is not None
    assert result.session_date == "2026-09-17"
    assert result.total_bars_evaluated == 0
    assert result.timeline == []
    assert result.replay_mode == "POSITION_MANAGER_REPLAY"

    # Summary performance metrics
    assert result.win_rate_pct >= 0.0
    assert result.total_trades == len(result.trades)
    assert result.winning_trades + result.losing_trades == result.total_trades


def test_simulation_overrides_cannot_bypass_missing_real_data():
    """Relaxing thresholds cannot generate trades from missing history."""
    engine = SimulationEngine()
    import asyncio

    # Strict overrides
    strict_req = SimulationRequest(
        date="2026-09-17",
        overrides=ThresholdOverrides(
            adx_threshold=35.0,
            rvol_threshold=2.5,
            min_confirmation_score=5,
            strat_b_min_confirmation=5,
        ),
        bypass_window=True,
    )
    res_strict = asyncio.run(engine.run_day_simulation(strict_req))

    # Relaxed overrides
    relaxed_req = SimulationRequest(
        date="2026-09-17",
        overrides=ThresholdOverrides(
            adx_threshold=10.0,
            rvol_threshold=0.8,
            min_confirmation_score=1,
            strat_b_min_confirmation=1,
        ),
        bypass_window=True,
    )
    res_relaxed = asyncio.run(engine.run_day_simulation(relaxed_req))

    assert res_relaxed.total_trades == res_strict.total_trades == 0
    assert res_relaxed.total_bars_evaluated == res_strict.total_bars_evaluated == 0


def test_simulation_available_dates():
    """Validates discovery of available trading session dates."""
    engine = SimulationEngine()
    import asyncio
    dates = asyncio.run(engine.get_available_dates())

    assert dates == []
    for d in dates:
        assert len(d) == 10
        assert d[4] == "-" and d[7] == "-"


@pytest.mark.asyncio
async def test_simulation_rest_endpoints():
    """Tests the REST API simulation endpoints."""
    container = await initialize_services()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Available dates
        res_dates = await client.get("/api/v1/strategies/simulate/available-dates")
        assert res_dates.status_code == 200
        dates_json = res_dates.json()
        assert "dates" in dates_json
        assert isinstance(dates_json["dates"], list)

        # 2. Run simulation
        payload = {
            "date": "2026-09-17",
            "instrument_id": "INST-NIFTY-INDEX",
            "bypass_window": True,
        }
        res_sim = await client.post("/api/v1/strategies/simulate", json=payload)
        assert res_sim.status_code == 200
        sim_json = res_sim.json()

        assert "total_bars_evaluated" in sim_json
        assert "total_trades" in sim_json
        assert "win_rate_pct" in sim_json
        assert "timeline" in sim_json
        assert "trades" in sim_json
        assert sim_json["replay_mode"] in {"SIGNALS_ONLY", "POSITION_MANAGER_REPLAY"}
        assert sim_json["total_trades"] == len(sim_json["trades"])

    await container.strategy_svc.stop()
