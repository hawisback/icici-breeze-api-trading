from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from services.strategy.models import (
    ReplayDataQuality,
    ReplayOptionMarkMetrics,
    ReplayPortfolioMetrics,
    ReplaySignalMetrics,
    ReplayUnderlyingLifecycleMetrics,
    RiskConfig,
    SimulationResult,
)
from services.strategy.replay_analytics import (
    build_portfolio_metrics,
    build_replay_run_identity,
    compare_replay_results,
)
from services.strategy.replay_execution import ChronologicalReplayExecutor
from services.strategy.replay_manifest import ReplayManifestRecord
from services.strategy.repository import StrategyRepository


UTC = timezone.utc
SESSION_START = datetime(2026, 9, 24, 3, 45, tzinfo=UTC)
SESSION_END = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


def _record(
    strategy: str,
    index: int,
    *,
    entry_minute: int,
    exit_minute: int,
    realized_r: float,
    gross_pnl: float | None,
    net_pnl: float | None,
) -> ReplayManifestRecord:
    entry = SESSION_START + timedelta(minutes=entry_minute)
    exit_time = SESSION_START + timedelta(minutes=exit_minute)
    return ReplayManifestRecord(
        replay_signal_id=f"SIG-{strategy}-{index}",
        strategy_id=strategy,
        direction="CALL" if index % 2 == 0 else "PUT",
        trading_date="2026-09-24",
        trigger_source_candle_timestamp=entry,
        trigger_level=100.0,
        simulated_entry_timestamp=entry,
        simulated_entry_price=100.0,
        entry_5m_candle_timestamp=entry,
        entry_occurred_intrabar=False,
        atr_at_entry=5.0,
        initial_structural_stop=95.0,
        initial_risk_points=5.0,
        initial_risk_atr=1.0,
        current_trailing_stop=95.0,
        current_r=0.0,
        peak_r=max(0.0, realized_r),
        protected_breakeven_active=False,
        profit_lock_active=False,
        runner_mode_active=False,
        current_ladder_stage="OPEN_INITIAL_RISK",
        reversal_score=0,
        entry_bar_timestamp=entry,
        lifecycle_status="RESOLVED",
        exit_timestamp=exit_time,
        exit_price=100.0 + realized_r * 5.0,
        exit_reason="TEST_EXIT",
        realized_r=realized_r,
        sizing_status="APPLIED",
        sizing_method="TEST",
        sizing_account_equity=500000.0,
        sizing_risk_budget=2500.0,
        sizing_entry_reference_price=100.0,
        replay_lots=1,
        replay_quantity=50,
        simulated_entry_fill_price=100.0,
        simulated_exit_fill_price=(
            100.0 + (gross_pnl or 0.0) / 50.0
            if gross_pnl is not None
            else None
        ),
        simulated_gross_pnl=gross_pnl,
        simulated_net_pnl=net_pnl,
    )


def _result(
    *,
    run_id: str,
    config_fp: str,
    data_fp: str,
    total_r: float,
    net_exec: float | None,
    max_drawdown_pnl: float | None,
) -> SimulationResult:
    return SimulationResult(
        replay_mode="EXECUTION_PARITY",
        session_date="2026-09-24",
        run_id=run_id,
        reproducibility={
            "run_id": run_id,
            "configuration_fingerprint": config_fp,
            "data_fingerprint": data_fp,
            "cost_model_version": "paper_options_costs_v1",
            "contract_selection_policy": (
                "PRODUCTION_CONTRACT_SELECTOR_WITH_EXPLICIT_APPROXIMATION"
            ),
        },
        signal_metrics=ReplaySignalMetrics(
            total_bars_evaluated=75,
            qualified_signals=5,
            ambiguous_signals=0,
            unresolved_signals=0,
        ),
        underlying_lifecycle_metrics=ReplayUnderlyingLifecycleMetrics(
            resolved_trades=5,
            winning_trades=3,
            losing_trades=2,
            breakeven_trades=0,
            win_rate_pct=60.0,
            total_realized_r=total_r,
            average_realized_r=total_r / 5,
            median_realized_r=0.5,
            average_winner_r=0.75,
            average_loser_r=-0.375,
            profit_factor_r=3.0,
            max_drawdown_r=-0.5,
            max_consecutive_losses=1,
        ),
        option_mark_metrics=ReplayOptionMarkMetrics(
            priced_trades=5,
            unpriced_trades=0,
            all_resolved_trades_priced=True,
            gross_mark_pnl=1800.0,
            estimated_transaction_costs=300.0,
            net_mark_pnl=1500.0,
            execution_estimated_trades=5,
            execution_unavailable_trades=0,
            gross_estimated_executable_pnl=1800.0,
            estimated_slippage_costs=100.0,
            estimated_execution_transaction_costs=200.0,
            net_estimated_executable_pnl=net_exec,
        ),
        portfolio_metrics=ReplayPortfolioMetrics(
            available=True,
            pnl_complete=net_exec is not None,
            starting_equity=500000.0,
            ending_equity=(
                500000.0 + net_exec if net_exec is not None else None
            ),
            net_executable_pnl=net_exec,
            max_drawdown_pnl=max_drawdown_pnl,
            expectancy_pnl=(
                net_exec / 5 if net_exec is not None else None
            ),
            expectancy_r=total_r / 5,
            exposure_pct=18.67,
            rejected_opportunities=2,
            strategy_realized_r={
                "TREND_PULLBACK": 1.0,
                "VOLATILITY_BREAKOUT": -0.5,
                "DI_CONTINUATION": 0.75,
                "SR_MOMENTUM_BREAKOUT": -0.25,
                "PIVOT_VWAP_SCALP": 0.5,
            },
            limitation=None,
        ),
        data_quality=ReplayDataQuality(historical_source="BREEZE"),
    )


def test_portfolio_metrics_cover_all_strategies_and_overlapping_exposure():
    records = [
        _record(
            "TREND_PULLBACK",
            0,
            entry_minute=15,
            exit_minute=45,
            realized_r=1.0,
            gross_pnl=1100.0,
            net_pnl=1000.0,
        ),
        _record(
            "VOLATILITY_BREAKOUT",
            1,
            entry_minute=25,
            exit_minute=35,
            realized_r=-0.5,
            gross_pnl=-400.0,
            net_pnl=-500.0,
        ),
        _record(
            "DI_CONTINUATION",
            2,
            entry_minute=50,
            exit_minute=65,
            realized_r=0.75,
            gross_pnl=850.0,
            net_pnl=750.0,
        ),
        _record(
            "SR_MOMENTUM_BREAKOUT",
            3,
            entry_minute=75,
            exit_minute=85,
            realized_r=-0.25,
            gross_pnl=-150.0,
            net_pnl=-250.0,
        ),
        _record(
            "PIVOT_VWAP_SCALP",
            4,
            entry_minute=90,
            exit_minute=105,
            realized_r=0.5,
            gross_pnl=600.0,
            net_pnl=500.0,
        ),
    ]
    metrics = build_portfolio_metrics(
        records,
        starting_equity=500000.0,
        session_start=SESSION_START,
        session_end=SESSION_END,
        execution_metadata={
            "daily_entries": 5,
            "daily_entries_by_strategy": {
                record.strategy_id: 1 for record in records
            },
            "entry_gate_block_counts": {
                "DAILY_TRADE_LIMIT_REACHED": 3,
                "LOSS_COOLDOWN_ACTIVE": 2,
            },
            "rejected_opportunities": [
                {"status": "CONTRACT_SELECTION_REJECTED"},
                {"status": "SIZING_REJECTED"},
            ],
        },
    )

    assert metrics.available is True
    assert metrics.pnl_complete is True
    assert metrics.ending_equity == 501500.0
    assert metrics.net_executable_pnl == 1500.0
    assert metrics.max_drawdown_pnl == -500.0
    assert metrics.max_drawdown_pct == -0.1
    assert metrics.max_drawdown_r == -0.5
    assert metrics.profit_factor_pnl == 3.0
    assert metrics.expectancy_pnl == 300.0
    assert metrics.expectancy_r == 0.3
    assert metrics.max_consecutive_wins == 1
    assert metrics.max_consecutive_losses == 1
    assert metrics.exposure_minutes == 70.0
    assert metrics.max_concurrent_positions == 2
    assert metrics.peak_premium_committed == 10000.0
    assert metrics.peak_risk_budget_committed == 5000.0
    assert metrics.rejected_opportunities == 2
    assert metrics.risk_gate_block_counts["LOSS_COOLDOWN_ACTIVE"] == 2
    assert set(metrics.strategy_realized_r) == {
        "TREND_PULLBACK",
        "VOLATILITY_BREAKOUT",
        "DI_CONTINUATION",
        "SR_MOMENTUM_BREAKOUT",
        "PIVOT_VWAP_SCALP",
    }
    assert len(metrics.equity_curve) == 6


def test_incomplete_execution_pnl_never_builds_partial_equity_curve():
    complete = _record(
        "TREND_PULLBACK",
        0,
        entry_minute=15,
        exit_minute=30,
        realized_r=1.0,
        gross_pnl=1100.0,
        net_pnl=1000.0,
    )
    missing = _record(
        "DI_CONTINUATION",
        1,
        entry_minute=35,
        exit_minute=50,
        realized_r=-1.0,
        gross_pnl=None,
        net_pnl=None,
    )

    metrics = build_portfolio_metrics(
        [complete, missing],
        starting_equity=500000.0,
        session_start=SESSION_START,
        session_end=SESSION_END,
        execution_metadata={},
    )

    assert metrics.available is True
    assert metrics.pnl_complete is False
    assert metrics.ending_equity is None
    assert metrics.net_executable_pnl is None
    assert metrics.max_drawdown_pnl is None
    assert metrics.max_drawdown_pct is None
    assert metrics.profit_factor_pnl is None
    assert metrics.expectancy_pnl is None
    assert metrics.max_drawdown_r == -1.0
    assert len(metrics.equity_curve) == 1
    assert "intentionally unavailable" in (metrics.limitation or "")


def test_replay_run_identity_is_deterministic_and_tracks_provenance():
    kwargs = {
        "session_date": "2026-09-24",
        "replay_mode": "EXECUTION_PARITY",
        "configuration_fingerprint": "cfg-123",
        "data_fingerprint": "data-456",
        "configuration_snapshot": {
            "strategy_a": {"evaluator_version": "trend_pullback_r5"},
            "strategy_suite": {
                "candidate_contracts": {
                    "DI_CONTINUATION": (
                        "STRATEGY_C_DI_CONTINUATION_V1_CANDIDATE"
                    ),
                    "SR_MOMENTUM_BREAKOUT": (
                        "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE"
                    ),
                }
            },
        },
        "cost_model_version": "paper_options_costs_v1",
        "contract_selection_policy": (
            "PRODUCTION_CONTRACT_SELECTOR_WITH_EXPLICIT_APPROXIMATION"
        ),
        "historical_source": "BREEZE",
        "contract_selection_evidence": {
            "POINT_IN_TIME_SNAPSHOT": 2,
            "APPROXIMATED_SELECTION": 3,
        },
        "execution_fill_methods": {
            "POINT_IN_TIME_BID_ASK": 2,
            "MARK_WITH_SLIPPAGE": 3,
        },
    }

    first = build_replay_run_identity(**kwargs)
    second = build_replay_run_identity(**kwargs)
    changed = build_replay_run_identity(
        **{**kwargs, "data_fingerprint": "data-789"}
    )

    assert first["run_id"] != second["run_id"]
    assert first["run_fingerprint"] == second["run_fingerprint"]
    assert first["run_fingerprint"] != changed["run_fingerprint"]
    assert first["strategy_versions"]["TREND_PULLBACK"] == "trend_pullback_r5"
    assert first["historical_source"] == "BREEZE"
    assert first["contract_selection_evidence"]["POINT_IN_TIME_SNAPSHOT"] == 2
    assert first["execution_fill_methods"]["MARK_WITH_SLIPPAGE"] == 3
    assert len(first["frozen_candidate_fingerprints"]["DI_CONTINUATION"]) == 64
    assert len(
        first["frozen_candidate_fingerprints"]["SR_MOMENTUM_BREAKOUT"]
    ) == 64
    assert (
        first["strategy_versions"]["SR_MOMENTUM_BREAKOUT"]
        == "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE"
    )


def test_execution_coordinator_blocks_second_simultaneous_entry_at_capacity():
    coordinator = ChronologicalReplayExecutor(
        lifecycle_replayer=Mock(),
        registry=Mock(),
        risk_config=RiskConfig(max_concurrent_positions=1),
    )
    coordinator.state.active_positions.append(Mock())
    at = datetime(2026, 9, 24, 5, 0, tzinfo=UTC)

    decision = coordinator.global_entry_gate(at)

    assert decision.allowed is False
    assert decision.status == "MAX_CONCURRENT_POSITIONS_REACHED"


def test_replay_comparison_reports_deltas_without_ranking():
    baseline = _result(
        run_id="RPL-BASE",
        config_fp="cfg-a",
        data_fp="data-same",
        total_r=1.5,
        net_exec=1500.0,
        max_drawdown_pnl=-500.0,
    )
    candidate = _result(
        run_id="RPL-CANDIDATE",
        config_fp="cfg-b",
        data_fp="data-same",
        total_r=2.0,
        net_exec=1800.0,
        max_drawdown_pnl=-650.0,
    )

    comparison = compare_replay_results(baseline, candidate)

    assert comparison["baseline_run_id"] == "RPL-BASE"
    assert comparison["candidate_run_id"] == "RPL-CANDIDATE"
    assert comparison["identity"]["same_data_fingerprint"] is True
    assert comparison["identity"]["same_configuration_fingerprint"] is False
    assert comparison["metrics"]["total_realized_r"]["delta"] == 0.5
    assert (
        comparison["metrics"]["net_estimated_executable_pnl"]["delta"]
        == 300.0
    )
    assert "winner" not in comparison
    assert "score" not in comparison


@pytest.mark.asyncio
async def test_replay_run_repository_round_trip_and_summary(tmp_path):
    repository = StrategyRepository(db_path=tmp_path / "strategy.db")
    await repository.initialize()
    result = _result(
        run_id="RPL-PERSISTED",
        config_fp="cfg-persisted",
        data_fp="data-persisted",
        total_r=1.5,
        net_exec=1500.0,
        max_drawdown_pnl=-500.0,
    )

    await repository.save_replay_run(result)
    loaded = await repository.get_replay_run("RPL-PERSISTED")
    summaries = await repository.list_replay_runs(limit=10)

    assert loaded is not None
    assert loaded.run_id == result.run_id
    assert loaded.portfolio_metrics.net_executable_pnl == 1500.0
    assert len(summaries) == 1
    assert summaries[0]["run_id"] == "RPL-PERSISTED"
    assert summaries[0]["configuration_fingerprint"] == "cfg-persisted"
    assert summaries[0]["net_estimated_executable_pnl"] == 1500.0
