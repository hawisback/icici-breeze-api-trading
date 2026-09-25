from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from libs.contracts.models import Candle
from services.instrument.service import InstrumentService
from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services
from services.historical.repository import HistoricalRepository
from services.strategy.models import (
    ReplayDataQuality,
    ReplayOptionMarkMetrics,
    ReplayPortfolioMetrics,
    ReplaySignalMetrics,
    ReplayUnderlyingLifecycleMetrics,
    SimulatedTradeRecord,
    SimulationRequest,
    SimulationResult,
    HistoricalReplaySource,
    RiskConfig,
    ThresholdOverrides,
)
from services.strategy.replay_lifecycle import (
    _historical_close_at,
    attach_historical_option_prices,
    build_lifecycle_report,
    build_simulated_trade_records,
    summarize_historical_option_marks,
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


def _canonical_sections(
    *,
    bars: int,
    qualified_signals: int,
    resolved: int,
    winners: int,
    losers: int,
    breakeven: int,
    win_rate_pct: float,
    total_r: float,
    profit_factor_r: float | None,
    max_drawdown_r: float,
    gross_mark_pnl: float | None = None,
    net_mark_pnl: float | None = None,
) -> dict:
    return {
        "signal_metrics": ReplaySignalMetrics(
            total_bars_evaluated=bars,
            qualified_signals=qualified_signals,
            ambiguous_signals=0,
            unresolved_signals=max(qualified_signals - resolved, 0),
        ),
        "underlying_lifecycle_metrics": ReplayUnderlyingLifecycleMetrics(
            resolved_trades=resolved,
            winning_trades=winners,
            losing_trades=losers,
            breakeven_trades=breakeven,
            win_rate_pct=win_rate_pct,
            total_realized_r=total_r,
            average_realized_r=round(total_r / resolved, 4) if resolved else 0.0,
            median_realized_r=0.0,
            average_winner_r=0.0,
            average_loser_r=0.0,
            profit_factor_r=profit_factor_r,
            max_drawdown_r=max_drawdown_r,
            max_consecutive_losses=0,
        ),
        "option_mark_metrics": ReplayOptionMarkMetrics(
            priced_trades=0 if gross_mark_pnl is None else resolved,
            unpriced_trades=resolved if gross_mark_pnl is None else 0,
            all_resolved_trades_priced=gross_mark_pnl is not None,
            gross_mark_pnl=gross_mark_pnl,
            estimated_transaction_costs=(
                None
                if gross_mark_pnl is None or net_mark_pnl is None
                else round(gross_mark_pnl - net_mark_pnl, 2)
            ),
            net_mark_pnl=net_mark_pnl,
        ),
        "portfolio_metrics": ReplayPortfolioMetrics(),
        "data_quality": ReplayDataQuality(historical_source="BREEZE"),
    }


def test_simulation_result_trades_are_the_canonical_summary_rows():
    """Resolved lifecycle records populate the same rows the UI renders."""
    records = [_resolved_manifest(1, 0.2), _resolved_manifest(2, 0.1), _resolved_manifest(3, -0.1611)]
    trades = build_simulated_trade_records(records)
    result = SimulationResult(
        session_date="2026-09-17",
        **_canonical_sections(
            bars=75,
            qualified_signals=3,
            resolved=3,
            winners=2,
            losers=1,
            breakeven=0,
            win_rate_pct=66.67,
            total_r=sum(t.realized_r for t in trades),
            profit_factor_r=1.86,
            max_drawdown_r=-0.1611,
        ),
        trades=trades,
    )

    assert result.total_trades == 3 == len(result.trades)
    assert result.winning_trades == 2
    assert result.losing_trades == 1
    assert all(trade.entry_premium is None for trade in result.trades)
    assert all(trade.net_pnl is None for trade in result.trades)
    assert summarize_simulated_pnl(result.trades) == (None, None)
    serialized = result.model_dump(mode="json")
    assert serialized["signal_metrics"]["price_basis"] == "COMPLETED_UNDERLYING_SPOT_FUTURES_CANDLES"
    assert serialized["underlying_lifecycle_metrics"]["profit_factor_r"] == 1.86
    assert serialized["option_mark_metrics"]["net_mark_pnl"] is None
    assert serialized["portfolio_metrics"]["available"] is False
    assert serialized["data_quality"]["historical_source"] == "BREEZE"
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
        **_canonical_sections(
            bars=75,
            qualified_signals=0,
            resolved=0,
            winners=0,
            losers=0,
            breakeven=0,
            win_rate_pct=0.0,
            total_r=0.0,
            profit_factor_r=None,
            max_drawdown_r=0.0,
            gross_mark_pnl=0.0,
            net_mark_pnl=0.0,
        ),
        trades=build_simulated_trade_records([]),
    )
    assert empty_result.total_trades == 0 == len(empty_result.trades)


def test_legacy_metric_fields_are_projections_of_canonical_sections():
    result = SimulationResult(
        session_date="2026-09-17",
        **_canonical_sections(
            bars=42,
            qualified_signals=4,
            resolved=3,
            winners=2,
            losers=1,
            breakeven=0,
            win_rate_pct=66.67,
            total_r=1.25,
            profit_factor_r=2.5,
            max_drawdown_r=-0.75,
            gross_mark_pnl=2500.0,
            net_mark_pnl=2200.0,
        ),
        total_bars_evaluated=999,
        total_trades=999,
        winning_trades=999,
        losing_trades=999,
        win_rate_pct=1.0,
        total_pnl=999.0,
        net_pnl=999.0,
        total_realized_r=999.0,
        max_drawdown_pnl=999.0,
        profit_factor=999.0,
        max_drawdown_r=999.0,
    )

    assert result.total_bars_evaluated == 42
    assert result.total_trades == 3
    assert result.winning_trades == 2
    assert result.losing_trades == 1
    assert result.win_rate_pct == 66.67
    assert result.total_pnl == 2500.0
    assert result.net_pnl == 2200.0
    assert result.total_realized_r == 1.25
    assert result.profit_factor == 2.5
    assert result.max_drawdown_r == -0.75
    assert result.max_drawdown_pnl is None


def test_lifecycle_report_exposes_realized_r_profit_factor_and_drawdown():
    records = [
        _resolved_manifest(1, 1.0),
        _resolved_manifest(2, -0.5),
        _resolved_manifest(3, -0.75),
        _resolved_manifest(4, 0.5),
    ]

    report = build_lifecycle_report(records, {"resolver": {}})

    assert report["total_r"] == 0.25
    assert report["profit_factor"] == 1.2
    assert report["max_drawdown_r"] == -1.25


def test_lifecycle_profit_factor_is_not_fabricated_without_losses():
    records = [_resolved_manifest(1, 0.5), _resolved_manifest(2, 1.0)]

    report = build_lifecycle_report(records, {"resolver": {}})

    assert report["profit_factor"] is None
    assert report["max_drawdown_r"] == 0.0


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
            start_time=record.simulated_entry_timestamp - timedelta(minutes=1),
            end_time=record.simulated_entry_timestamp,
            open=120.0, high=121.0, low=119.0, close=120.0, volume=100,
            source="BREEZE",
        ),
        Candle(
            instrument_id=contract.instrument_id, interval="1m",
            start_time=record.simulated_entry_timestamp,
            end_time=record.simulated_entry_timestamp + timedelta(minutes=1),
            open=999.0, high=999.0, low=999.0, close=999.0, volume=100,
            source="BREEZE",
        ),
        Candle(
            instrument_id=contract.instrument_id, interval="1m",
            start_time=record.exit_timestamp - timedelta(minutes=1),
            end_time=record.exit_timestamp,
            open=110.0, high=111.0, low=109.0, close=110.0, volume=100,
            source="BREEZE",
        ),
        Candle(
            instrument_id=contract.instrument_id, interval="1m",
            start_time=record.exit_timestamp,
            end_time=record.exit_timestamp + timedelta(minutes=1),
            open=888.0, high=888.0, low=888.0, close=888.0, volume=100,
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
    assert record.historical_option_provenance["pricing_field"] == "completed_candle_close"
    assert record.historical_option_provenance["entry"]["candle_end"] == record.simulated_entry_timestamp.isoformat()
    assert record.historical_option_provenance["exit"]["candle_end"] == record.exit_timestamp.isoformat()
    assert record.historical_option_provenance["entry"]["mark_age_seconds"] == 0.0
    assert record.historical_option_provenance["bid_ask_available"] is False
    assert record.historical_option_provenance["executable_fill_equivalent"] is False

    mark_summary = summarize_historical_option_marks([record])
    assert mark_summary["priced_trades"] == 1
    assert mark_summary["unpriced_trades"] == 0
    assert mark_summary["all_resolved_trades_priced"] is True
    assert mark_summary["gross_mark_pnl"] == trade.gross_pnl
    assert mark_summary["estimated_transaction_costs"] == record.option_transaction_costs
    assert mark_summary["net_mark_pnl"] == trade.net_pnl


def test_option_mark_summary_never_partially_aggregates_missing_marks():
    priced = _resolved_manifest(1, 0.5)
    priced.option_data_status = "AVAILABLE"
    priced.option_gross_pnl = 100.0
    priced.option_transaction_costs = 20.0
    priced.option_net_pnl = 80.0

    missing = _resolved_manifest(2, -0.5)
    missing.option_data_status = "UNAVAILABLE"
    missing.option_data_quality_reason = "Historical option candle missing"

    summary = summarize_historical_option_marks([priced, missing])

    assert summary["priced_trades"] == 1
    assert summary["unpriced_trades"] == 1
    assert summary["all_resolved_trades_priced"] is False
    assert summary["gross_mark_pnl"] is None
    assert summary["estimated_transaction_costs"] is None
    assert summary["net_mark_pnl"] is None
    assert summary["quality_reasons"] == {"Historical option candle missing": 1}


def _option_candle(start: datetime, close: float) -> Candle:
    return Candle(
        instrument_id="OPTION",
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1,
        source="BREEZE",
    )


def test_historical_close_at_rejects_candle_at_event_start():
    event = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
    assert _historical_close_at([_option_candle(event, 100.0)], event) is None


def test_historical_close_at_rejects_candle_during_event():
    start = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
    assert _historical_close_at([_option_candle(start, 100.0)], start + timedelta(seconds=30)) is None


def test_historical_close_at_allows_exact_completion_boundary():
    start = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
    assert _historical_close_at([_option_candle(start, 100.0)], start + timedelta(minutes=1)) == 100.0


def test_historical_close_at_uses_most_recent_completed_candle():
    start = datetime(2026, 9, 17, 9, 59, tzinfo=timezone.utc)
    event = start + timedelta(seconds=90)
    candles = [_option_candle(start, 95.0), _option_candle(start + timedelta(minutes=1), 100.0)]
    assert _historical_close_at(candles, event) == 95.0


def test_historical_close_at_never_selects_future_only_candles():
    event = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
    assert _historical_close_at([_option_candle(event + timedelta(minutes=1), 100.0)], event) is None


def test_historical_close_at_ignores_invalid_close_and_falls_back():
    start = datetime(2026, 9, 17, 9, 58, tzinfo=timezone.utc)
    candles = [_option_candle(start, 95.0), _option_candle(start + timedelta(minutes=1), 0.0)]
    assert _historical_close_at(candles, start + timedelta(minutes=2)) == 95.0


def test_historical_close_at_normalizes_aware_timezones():
    event = datetime(2026, 9, 17, 15, 31, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    start = datetime(2026, 9, 17, 9, 59, tzinfo=timezone.utc)
    assert _historical_close_at([_option_candle(start, 100.0)], event) == 100.0


@pytest.mark.asyncio
async def test_available_simulation_dates_returns_only_sessions_with_futures(tmp_path):
    repository = HistoricalRepository(tmp_path / "historical.db")
    await repository.initialize()
    candles = []
    for index, day in enumerate((15, 16, 17, 18)):
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
        # September 18 intentionally has spot only. Strategy A must not offer
        # a replay date unless at least one complete 15m futures bucket exists.
        if day != 18:
            for offset in range(3):
                future_start = start + timedelta(minutes=5 * offset)
                candles.append(
                    Candle(
                        instrument_id="INST-NIFTY-FUT-2026-09-29",
                        interval="5m",
                        start_time=future_start,
                        end_time=future_start + timedelta(minutes=5),
                        open=200.0 + index,
                        high=201.0 + index,
                        low=199.0 + index,
                        close=200.5 + index,
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


def test_replay_metadata_discloses_applied_and_ignored_controls():
    engine = SimulationEngine()

    request = SimulationRequest(
        date="2026-09-17",
        capital=750000.0,
        max_trades_per_day=2,
        bypass_window=True,
        overrides=ThresholdOverrides(
            adx_threshold=30.0,
            rvol_threshold=1.4,
            strat_b_min_confirmation=4,
            box_max_height_atr=1.5,
            max_option_premium_cap=85.0,
        ),
    )

    import asyncio
    result = asyncio.run(engine.run_day_simulation(request))
    controls = result.replay_metadata["control_application"]

    assert controls["applied_overrides"]["rvol_threshold"] == 1.4
    assert controls["applied_overrides"]["strat_b_min_confirmation"] == 4
    assert controls["applied_overrides"]["box_max_height_atr"] == 1.5
    assert controls["applied_overrides"]["bypass_entry_window"] is True
    assert "adx_threshold" in controls["not_applied_overrides"]
    assert "max_option_premium_cap" in controls["not_applied_overrides"]
    assert controls["not_applied_request_controls"]["capital"]["value"] == 750000.0
    assert controls["not_applied_request_controls"]["max_trades_per_day"]["value"] == 2

    snapshot_overrides = result.replay_metadata["configuration_snapshot"]["threshold_overrides"]
    assert snapshot_overrides["rvol_threshold"] == 1.4
    assert snapshot_overrides["strat_b_min_confirmation"] == 4
    assert snapshot_overrides["box_max_height_atr"] == 1.5
    assert snapshot_overrides["bypass_entry_window"] is True
    assert snapshot_overrides.get("adx_threshold") is None
    assert snapshot_overrides.get("max_option_premium_cap") is None


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


@pytest.mark.asyncio
async def test_replay_fetches_exact_target_window_instead_of_days_back_from_now(tmp_path):
    repository = HistoricalRepository(tmp_path / "historical.db")
    await repository.initialize()
    target_start = datetime(2026, 9, 17, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc)
    replay_candle = Candle(
        instrument_id="INST-NIFTY-INDEX", interval="5m",
        start_time=target_start, end_time=target_start + timedelta(minutes=5),
        open=100, high=102, low=99, close=101, volume=1000, source="BREEZE",
    )
    fetch = AsyncMock(return_value=[replay_candle])
    hist = SimpleNamespace(repo=repository, fetch_candles_from_provider_window=fetch)
    engine = SimulationEngine(historical_service=hist)
    diagnostics = {}
    warmup, session = await engine._fetch_session_candles(
        "2026-09-17", "INST-NIFTY-INDEX",
        historical_source=HistoricalReplaySource.BREEZE,
        source_diagnostics=diagnostics, role="spot",
    )
    assert warmup == []
    assert session == [replay_candle]
    kwargs = fetch.await_args.kwargs
    assert kwargs["requested_source"] == "BREEZE"
    assert kwargs["start_time"].astimezone(timezone(timedelta(hours=5, minutes=30))).date().isoformat() == "2026-09-10"
    assert kwargs["end_time"].astimezone(timezone(timedelta(hours=5, minutes=30))).date().isoformat() == "2026-09-17"
    assert diagnostics["spot"]["targeted_fetch_count"] == 1


@pytest.mark.asyncio
async def test_replay_seeds_and_resolves_futures_for_target_date_not_today():
    future = SimpleNamespace(
        instrument_id="INST-NIFTY-FUT-2026-09-29", segment="FUTURES",
        tradable=True, expiry="2026-09-29",
    )
    ensure = AsyncMock(return_value=[future])
    repo = SimpleNamespace(search=AsyncMock(side_effect=[[], [future]]))
    hist = SimpleNamespace(instrument_service=SimpleNamespace(ensure_current_nifty_futures=ensure, repo=repo))
    engine = SimulationEngine(historical_service=hist)
    diagnostics = {}
    instrument_id, contracts = await engine._resolve_replay_futures_instrument(
        "2026-09-17", source_diagnostics=diagnostics
    )
    assert instrument_id == "INST-NIFTY-FUT-2026-09-29"
    ensure.assert_awaited_once()
    assert ensure.await_args.kwargs["today"].isoformat() == "2026-09-17"
    assert contracts[0]["expiry"] == "2026-09-29"
    assert diagnostics["futures_contract"]["status"] == "RESOLVED"
    assert diagnostics["futures_contract"]["resolution_source"] == "DETERMINISTIC_FALLBACK"


@pytest.mark.asyncio
async def test_replay_prefers_actual_historical_futures_contract_over_seeded_metadata(tmp_path):
    repository = HistoricalRepository(tmp_path / "historical.db")
    await repository.initialize()
    start = datetime(2025, 1, 15, 9, 15, tzinfo=timezone.utc)
    await repository.save_candles([
        Candle(
            instrument_id="INST-NIFTY-FUT-2025-01-30",
            interval="5m",
            start_time=start + timedelta(minutes=5 * offset),
            end_time=start + timedelta(minutes=5 * (offset + 1)),
            open=200.0,
            high=201.0,
            low=199.0,
            close=200.5,
            volume=1000,
            source="BREEZE",
        )
        for offset in range(3)
    ])
    wrong_seed = SimpleNamespace(
        instrument_id="INST-NIFTY-FUT-2025-01-28", segment="FUTURES",
        tradable=True, expiry="2025-01-28",
    )
    ensure = AsyncMock(return_value=[wrong_seed])
    instrument_repo = SimpleNamespace(search=AsyncMock(return_value=[wrong_seed]))
    hist = SimpleNamespace(
        repo=repository,
        instrument_service=SimpleNamespace(ensure_current_nifty_futures=ensure, repo=instrument_repo),
    )
    engine = SimulationEngine(historical_service=hist)
    diagnostics = {}

    instrument_id, contracts = await engine._resolve_replay_futures_instrument(
        "2025-01-15",
        source_diagnostics=diagnostics,
        historical_source=HistoricalReplaySource.BREEZE,
    )

    assert instrument_id == "INST-NIFTY-FUT-2025-01-30"
    assert contracts == [{
        "instrument_id": "INST-NIFTY-FUT-2025-01-30",
        "expiry": "2025-01-30",
    }]
    assert diagnostics["futures_contract"]["resolution_source"] == "HISTORICAL_STORAGE"
    ensure.assert_not_awaited()
    instrument_repo.search.assert_not_awaited()


def test_nifty_monthly_expiry_fallback_respects_2025_weekday_transition():
    assert InstrumentService._monthly_expiry(2025, 8) == date(2025, 8, 28)
    assert InstrumentService._monthly_expiry(2025, 9) == date(2025, 9, 30)


def test_strategy_a_replay_ignores_legacy_hard_adx_override():
    engine = SimulationEngine()
    overrides = ThresholdOverrides(adx_threshold=17.0)
    config = engine._strategy_a_config_for_replay(overrides)
    assert config.adx_threshold == engine.tunables.adx_threshold
    assert config.confirmation_min_body_ratio == engine.tunables.confirmation_min_body_ratio


def test_strategy_a_futures_coverage_reports_missing_entry_window_bar():
    engine = SimulationEngine()
    ist = timezone(timedelta(hours=5, minutes=30))
    target = datetime(2026, 9, 15, 9, 30, tzinfo=ist)
    candles = []
    # Build all 5m bars required for 09:45 and 10:00, but intentionally omit
    # the 09:50 bar so the completed 10:00 15m bucket is unavailable.
    for minute in (30, 35, 40, 45, 55):
        start = target.replace(hour=9, minute=minute)
        candles.append(Candle(
            instrument_id="INST-NIFTY-FUT-2026-09-29", interval="5m",
            start_time=start.astimezone(timezone.utc),
            end_time=(start + timedelta(minutes=5)).astimezone(timezone.utc),
            open=100, high=102, low=99, close=101, volume=100, source="BREEZE",
        ))
    coverage = engine._strategy_a_futures_coverage(
        "2026-09-15", candles, "INST-NIFTY-FUT-2026-09-29"
    )
    assert coverage["expected_15m_bars"] > 0
    assert coverage["coverage_pct"] < 100
    assert any(value.startswith("2026-09-15T10:00:00") for value in coverage["missing_15m_bar_ends_ist"])


def test_available_dates_query_exposes_more_than_legacy_30_session_cap():
    import inspect
    source = inspect.getsource(SimulationEngine.get_available_dates)
    assert "LIMIT 120" in source


@pytest.mark.asyncio
async def test_strategy_a_replay_cached_futures_universe_preserves_rollover_warmup(tmp_path):
    repository = HistoricalRepository(tmp_path / "historical.db")
    await repository.initialize()

    prior_contract = "INST-NIFTY-FUT-2026-06-30"
    current_contract = "INST-NIFTY-FUT-2026-07-28"
    prior_start = datetime(2026, 6, 30, 3, 45, tzinfo=timezone.utc)
    current_start = datetime(2026, 7, 2, 3, 45, tzinfo=timezone.utc)

    candles = []
    for instrument_id, start, base in (
        (prior_contract, prior_start, 24000.0),
        (current_contract, current_start, 24100.0),
    ):
        for offset in range(3):
            candle_start = start + timedelta(minutes=5 * offset)
            candles.append(
                Candle(
                    instrument_id=instrument_id,
                    interval="5m",
                    start_time=candle_start,
                    end_time=candle_start + timedelta(minutes=5),
                    open=base + offset,
                    high=base + offset + 2,
                    low=base + offset - 2,
                    close=base + offset + 1,
                    volume=100,
                    open_interest=1000,
                    source="BREEZE",
                )
            )
    await repository.save_candles(candles)

    engine = SimulationEngine(historical_service=SimpleNamespace(repo=repository))
    diagnostics = {}
    history = await engine._fetch_cached_futures_universe(
        "2026-07-02",
        historical_source=HistoricalReplaySource.BREEZE,
        source_diagnostics=diagnostics,
    )

    assert {c.instrument_id for c in history} == {prior_contract, current_contract}
    canonical = diagnostics["futures"]["strategy_a_canonical_history"]
    assert canonical["contract_count"] == 2
    assert canonical["contracts"] == [prior_contract, current_contract]
    assert canonical["cache_only"] is True
