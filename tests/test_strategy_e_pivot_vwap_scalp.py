"""Focused coverage for Strategy E Pivot/VWAP scalp."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from libs.contracts.models import Candle
from services.strategy.execution_policy import resolve_strategy_execution_policy
from services.historical.service import HistoricalService
from services.strategy.models import (
    ActiveTrade,
    AutoTradingConfig,
    AutoTradingMode,
    MarketFeatures,
    OptionType,
    StrategyName,
    StrategyTunablesConfig,
    TradeDirection,
)
from services.strategy.service import StrategyService
from services.strategy.strategies.pivot_vwap_scalp import (
    PivotVwapScalpStrategy,
    StrategyEDecision,
)


IST = timezone(timedelta(hours=5, minutes=30))


def _candle(
    day: int,
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 1000,
    instrument_id: str = "INST-NIFTY-FUT-TEST",
) -> Candle:
    start = datetime(2026, 9, day, 10, 0, tzinfo=IST) + timedelta(
        minutes=5 * index
    )
    return Candle(
        instrument_id=instrument_id,
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        open_interest=100000,
        source="KITE",
    )


def _previous_session() -> list[Candle]:
    # Previous-day H/L/C -> 101 / 99 / 100 => Pivot 100.
    rows: list[Candle] = []
    for index in range(20):
        rows.append(
            _candle(
                23,
                index,
                open_=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000,
            )
        )
    return rows


def _strategy(**updates) -> PivotVwapScalpStrategy:
    values = {
        "pivot_vwap_scalp_enabled": True,
        "strategy_e_swing_lookback": 1,
        "strategy_e_volume_lookback": 5,
        "strategy_e_stop_buffer_points": 2.0,
        "strategy_e_max_stop_points": 30.0,
        "strategy_e_trend_target_points": 20.0,
        "strategy_e_counter_target_points": 10.0,
        "strategy_e_min_reward_risk": 1.0,
        "strategy_e_min_room_to_level_points": 5.0,
        "strategy_e_chop_cross_threshold": 3,
        "strategy_e_flat_vwap_threshold_points": 0.1,
    }
    values.update(updates)
    return PivotVwapScalpStrategy(StrategyTunablesConfig(**values))


def test_strategy_e_defaults_enabled_and_live_policy_is_promoted():
    config = StrategyTunablesConfig()
    assert config.pivot_vwap_scalp_enabled is True
    assert config.strategy_e_max_signal_age_seconds == 180.0

    service = StrategyService(oms_service=Mock(), repository=Mock())
    now = datetime(2026, 9, 25, 13, 30, tzinfo=IST)
    assert service._strategy_e_signal_data_fresh(
        now - timedelta(seconds=150),
        now=now,
    ) is True
    assert service._strategy_e_signal_data_fresh(
        now - timedelta(seconds=180),
        now=now,
    ) is True
    assert service._strategy_e_signal_data_fresh(
        now - timedelta(seconds=180.001),
        now=now,
    ) is False

    policy = resolve_strategy_execution_policy(
        StrategyName.PIVOT_VWAP_SCALP,
        AutoTradingMode.LIVE,
    )
    assert policy.call_mode is AutoTradingMode.LIVE
    assert policy.put_mode is AutoTradingMode.LIVE
    assert policy.live_trading_allowed is True
    assert policy.force_entry_allowed is False
    assert policy.promotion_state == "LIVE_PROMOTED"


def test_strategy_e_freshness_uses_provider_safe_completed_boundary():
    hist = HistoricalService()
    service = StrategyService(
        oms_service=Mock(),
        repository=Mock(),
        historical_service=hist,
    )
    now = datetime(2026, 9, 25, 13, 40, 30, tzinfo=IST)
    expected = hist.expected_completed_end("5m", now)

    assert expected == datetime(2026, 9, 25, 13, 35, tzinfo=IST).astimezone(
        timezone.utc
    )
    assert (now.astimezone(timezone.utc) - expected).total_seconds() > 180
    assert service._strategy_e_signal_data_fresh(
        expected,
        now=now,
        expected_completed_end=expected,
    ) is True

    one_bar_behind = expected - timedelta(minutes=5)
    assert service._strategy_e_signal_data_fresh(
        one_bar_behind,
        now=now,
        expected_completed_end=expected,
    ) is False


def test_strategy_e_rejects_counter_target_larger_than_trend_target():
    with pytest.raises(ValidationError, match="countertrend target"):
        StrategyTunablesConfig(
            strategy_e_trend_target_points=10.0,
            strategy_e_counter_target_points=11.0,
        )


def test_strategy_e_emits_trend_long_and_deduplicates_completed_bar():
    strategy = _strategy()
    current = [
        _candle(24, 0, open_=100.2, high=101.0, low=100.0, close=100.6),
        _candle(24, 1, open_=100.6, high=103.0, low=100.5, close=102.0),
        _candle(24, 2, open_=102.0, high=102.2, low=100.2, close=100.8),
        _candle(24, 3, open_=100.8, high=101.2, low=99.8, close=100.5),
        _candle(24, 4, open_=100.5, high=102.0, low=100.4, close=101.6),
        _candle(
            24,
            5,
            open_=101.6,
            high=104.0,
            low=101.3,
            close=103.5,
            volume=1400,
        ),
    ]
    bars = [*_previous_session(), *current]
    # Around the next 5-minute boundary, the provider-safe latest completed
    # bar can be more than 180 seconds old in wall-clock terms. If it matches
    # the expected completed boundary it must still be evaluated.
    decision = strategy.evaluate(
        bars,
        as_of=current[-1].end_time + timedelta(seconds=330),
        expected_completed_end=current[-1].end_time,
    )

    assert decision.result == "TREND_LONG"
    assert decision.signal is not None
    assert decision.signal.strategy is StrategyName.PIVOT_VWAP_SCALP
    assert decision.signal.direction is TradeDirection.BULLISH
    assert decision.signal.option_type is OptionType.CALL
    assert decision.metrics["pivot"] == 100.0
    assert decision.metrics["swing_high"] == 103.0
    assert decision.metrics["swing_low"] == 99.8
    assert decision.metrics["relative_volume"] > 1.2
    assert decision.signal.structural_stop == 97.8
    assert decision.signal.features_snapshot["target_price"] == 123.5

    duplicate = strategy.evaluate(
        bars,
        as_of=current[-1].end_time + timedelta(seconds=331),
        expected_completed_end=current[-1].end_time,
    )
    assert duplicate.signal is None
    assert duplicate.reason == "NO_NEW_COMPLETED_5M_BAR"


def test_strategy_e_emits_trend_short():
    strategy = _strategy()
    current = [
        _candle(24, 0, open_=99.8, high=100.0, low=99.0, close=99.4),
        _candle(24, 1, open_=99.4, high=99.8, low=97.0, close=98.0),
        _candle(24, 2, open_=98.0, high=99.5, low=97.8, close=99.0),
        _candle(24, 3, open_=99.0, high=100.2, low=98.5, close=99.5),
        _candle(24, 4, open_=99.5, high=99.8, low=98.0, close=98.5),
        _candle(
            24,
            5,
            open_=98.5,
            high=99.0,
            low=96.0,
            close=96.5,
            volume=1400,
        ),
    ]
    decision = strategy.evaluate(
        [*_previous_session(), *current],
        as_of=current[-1].end_time,
    )

    assert decision.result == "TREND_SHORT"
    assert decision.signal is not None
    assert decision.signal.direction is TradeDirection.BEARISH
    assert decision.signal.option_type is OptionType.PUT
    assert decision.metrics["swing_low"] == 97.0
    assert decision.metrics["swing_high"] == 100.2
    assert decision.signal.structural_stop == 102.2
    assert decision.signal.features_snapshot["target_price"] == 76.5



def test_strategy_e_rejects_stale_completed_bar_and_marks_it_consumed():
    strategy = _strategy(strategy_e_max_signal_age_seconds=30.0)
    current = [
        _candle(24, 0, open_=100.2, high=101.0, low=100.0, close=100.6),
        _candle(24, 1, open_=100.6, high=103.0, low=100.5, close=102.0),
        _candle(24, 2, open_=102.0, high=102.2, low=100.2, close=100.8),
        _candle(24, 3, open_=100.8, high=101.2, low=99.8, close=100.5),
        _candle(24, 4, open_=100.5, high=102.0, low=100.4, close=101.6),
        _candle(24, 5, open_=101.6, high=104.0, low=101.3, close=103.5),
    ]
    bars = [*_previous_session(), *current]
    stale = strategy.evaluate(
        bars,
        as_of=current[-1].end_time + timedelta(seconds=31),
    )
    assert stale.signal is None
    assert stale.reason == "STALE_COMPLETED_5M_BAR"

    duplicate = strategy.evaluate(
        bars,
        as_of=current[-1].end_time + timedelta(seconds=32),
    )
    assert duplicate.signal is None
    assert duplicate.reason == "NO_NEW_COMPLETED_5M_BAR"


def test_strategy_e_volume_is_confirmation_not_a_hard_gate():
    strategy = _strategy()
    current = [
        _candle(24, 0, open_=100.2, high=101.0, low=100.0, close=100.6),
        _candle(24, 1, open_=100.6, high=103.0, low=100.5, close=102.0),
        _candle(24, 2, open_=102.0, high=102.2, low=100.2, close=100.8),
        _candle(24, 3, open_=100.8, high=101.2, low=99.8, close=100.5),
        _candle(24, 4, open_=100.5, high=102.0, low=100.4, close=101.6),
        _candle(
            24,
            5,
            open_=101.6,
            high=104.0,
            low=101.3,
            close=103.5,
            volume=800,
        ),
    ]
    decision = strategy.evaluate(
        [*_previous_session(), *current],
        as_of=current[-1].end_time,
    )

    assert decision.result == "TREND_LONG"
    assert decision.signal is not None
    assert decision.metrics["relative_volume"] < 1.2
    assert decision.metrics["volume_confirmed"] is False


def test_strategy_e_emits_countertrend_long_from_support_micro_reversal():
    strategy = _strategy(
        strategy_e_stop_buffer_points=0.5,
        strategy_e_sr_buffer_points=0.5,
        strategy_e_counter_zone_points=2.5,
        strategy_e_chop_cross_threshold=4,
        strategy_e_min_reward_risk=0.2,
        strategy_e_min_room_to_level_points=0.5,
    )
    current = [
        _candle(24, 0, open_=97.0, high=97.6, low=96.5, close=97.0),
        _candle(24, 1, open_=97.0, high=98.0, low=96.8, close=97.5),
        _candle(24, 2, open_=97.5, high=97.8, low=95.5, close=96.2),
        _candle(24, 3, open_=96.2, high=97.0, low=96.0, close=96.6),
        _candle(24, 4, open_=96.6, high=97.2, low=96.0, close=96.4),
        _candle(
            24,
            5,
            open_=96.4,
            high=98.5,
            low=96.2,
            close=98.0,
            volume=1100,
        ),
    ]
    decision = strategy.evaluate(
        [*_previous_session(), *current],
        as_of=current[-1].end_time,
    )

    assert decision.result == "COUNTER_LONG"
    assert decision.signal is not None
    assert decision.signal.direction is TradeDirection.BULLISH
    assert decision.signal.option_type is OptionType.CALL
    assert decision.metrics["counter_confirmation"] in {
        "VWAP_RECLAIM",
        "BULLISH_MICRO_SWING",
    }
    assert decision.metrics["price"] < decision.metrics["pivot"]


def _active_e_trade(mode: AutoTradingMode) -> ActiveTrade:
    now = datetime(2026, 9, 24, 11, 0, tzinfo=IST)
    return ActiveTrade(
        trade_id="TRD-E-1",
        mode=mode,
        strategy=StrategyName.PIVOT_VWAP_SCALP,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        contract_symbol="NIFTYTESTCE",
        contract_instrument_id="INST-NIFTY-TEST-CE",
        expiry="2026-09-29",
        strike=25000.0,
        quantity=65,
        lot_size=65,
        lots=1,
        entry_time=now,
        entry_option_price=100.0,
        entry_spot_price=103.0,
        initial_structural_stop=98.0,
        initial_r_points=5.0,
        strategy_signal_type="TREND_LONG",
        strategy_target_price=110.0,
        current_option_price=100.0,
        current_spot_price=103.0,
        current_trailing_stop=98.0,
        option_hard_stop_price=75.0,
        futures_contract_id="INST-NIFTY-FUT-TEST",
        underlying_entry_price=103.0,
        underlying_current_price=103.0,
        underlying_structural_stop=98.0,
        underlying_r=5.0,
        filled_quantity=65 if mode == AutoTradingMode.LIVE else 0,
        initial_quantity=65,
        remaining_quantity=65,
    )


@pytest.mark.asyncio
async def test_strategy_e_live_stop_routes_through_shared_final_exit():
    now = datetime.now(timezone.utc)
    repo = SimpleNamespace(save_trade=AsyncMock())
    service = StrategyService(oms_service=Mock(), repository=repo)
    service._log_decision = AsyncMock()
    service._submit_live_final_exit = AsyncMock()
    service.mkt_svc = SimpleNamespace(
        get_latest_quote=Mock(
            return_value=SimpleNamespace(
                source="KITE",
                last_price=97.0,
                timestamp=now,
            )
        )
    )
    trade = _active_e_trade(AutoTradingMode.LIVE)
    quote = {
        "status": "VALID",
        "bid": 92.0,
        "ask": 92.5,
        "ltp": 92.2,
    }
    features = MarketFeatures(
        timestamp=now,
        spot_price=25000.0,
    )

    await service._evaluate_strategy_e_active_trade(
        trade,
        features,
        quote,
    )

    assert trade.pending_exit_reason == "STRATEGY_E_STOP_LOSS"
    assert trade.underlying_exit_price == 98.0
    service._submit_live_final_exit.assert_awaited_once()
    assert (
        service._submit_live_final_exit.await_args.args[3]
        == "STRATEGY_E_STOP_LOSS"
    )


@pytest.mark.asyncio
async def test_strategy_e_paper_ambiguous_bar_uses_stop_before_target():
    repo = SimpleNamespace(save_trade=AsyncMock())
    service = StrategyService(oms_service=Mock(), repository=repo)
    service._log_decision = AsyncMock()
    service._close_trade = AsyncMock()
    service.mkt_svc = None

    trade = _active_e_trade(AutoTradingMode.PAPER)
    candle = _candle(
        24,
        12,
        open_=104.0,
        high=112.0,
        low=96.0,
        close=105.0,
        volume=1200,
    )
    service._strategy_e_futures_5m = [candle]
    quote = {
        "status": "VALID",
        "bid": 99.0,
        "ask": 99.5,
        "ltp": 99.2,
    }
    features = MarketFeatures(
        timestamp=candle.end_time,
        spot_price=25000.0,
    )

    await service._evaluate_strategy_e_active_trade(
        trade,
        features,
        quote,
    )

    assert trade.pending_exit_reason == "STRATEGY_E_STOP_LOSS"
    assert trade.underlying_exit_price == 98.0
    service._close_trade.assert_awaited_once()
    assert service._close_trade.await_args.args[3] == "STRATEGY_E_STOP_LOSS"


def test_strategy_e_restore_state_restores_last_decision_without_resurrecting_signal():
    strategy = _strategy()
    strategy.last_processed_candle = datetime(2026, 9, 24, 10, 30, tzinfo=IST)
    strategy.last_decision = StrategyEDecision(
        "NO_TRADE",
        "NO_SETUP",
        {"price": 101.25, "pivot": 100.0},
    )

    restored = _strategy()
    restored.restore_state(strategy.export_state())

    assert restored.last_processed_candle == strategy.last_processed_candle
    assert restored.last_decision.result == "NO_TRADE"
    assert restored.last_decision.reason == "NO_SETUP"
    assert restored.last_decision.metrics["price"] == 101.25
    assert restored.last_decision.signal is None


def test_strategy_e_snapshot_analysis_does_not_consume_execution_candle():
    strategy = _strategy()
    current = [
        _candle(24, 0, open_=100.2, high=101.0, low=100.0, close=100.6),
        _candle(24, 1, open_=100.6, high=103.0, low=100.5, close=102.0),
        _candle(24, 2, open_=102.0, high=102.2, low=100.2, close=100.8),
        _candle(24, 3, open_=100.8, high=101.2, low=99.8, close=100.5),
        _candle(24, 4, open_=100.5, high=102.0, low=100.4, close=101.6),
        _candle(
            24,
            5,
            open_=101.6,
            high=104.0,
            low=101.3,
            close=103.5,
            volume=1400,
        ),
    ]
    bars = [*_previous_session(), *current]

    decision = strategy.analyze_snapshot(
        bars,
        as_of=current[-1].end_time,
        expected_completed_end=current[-1].end_time,
    )

    assert decision.result == "TREND_LONG"
    assert decision.signal is not None
    assert strategy.last_processed_candle is None
    assert strategy.last_decision.reason == "NOT_EVALUATED"


def test_strategy_e_passive_analysis_runs_outside_entry_window():
    strategy = _strategy()
    current = [
        _candle(24, 0, open_=100.2, high=101.0, low=100.0, close=100.6),
        _candle(24, 1, open_=100.6, high=103.0, low=100.5, close=102.0),
        _candle(24, 2, open_=102.0, high=102.2, low=100.2, close=100.8),
        _candle(24, 3, open_=100.8, high=101.2, low=99.8, close=100.5),
        _candle(24, 4, open_=100.5, high=102.0, low=100.4, close=101.6),
        _candle(
            24,
            5,
            open_=101.6,
            high=104.0,
            low=101.3,
            close=103.5,
            volume=1400,
        ),
    ]
    bars = [*_previous_session(), *current]
    after_hours = datetime(2026, 9, 24, 15, 20, tzinfo=IST)

    hist = SimpleNamespace(
        expected_completed_end=Mock(return_value=current[-1].end_time),
    )
    service = StrategyService(
        oms_service=Mock(),
        repository=Mock(),
        historical_service=hist,
    )
    service.config = AutoTradingConfig(
        tunables=strategy.config,
        mode=AutoTradingMode.PAPER,
    )
    service.strategy_e = strategy
    service._strategy_e_futures_5m = bars

    assert service._strategy_e_entry_window_open(after_hours) is False

    decision = service._refresh_strategy_e_analysis(after_hours)

    assert decision.result == "TREND_LONG"
    assert decision.signal is not None
    assert service._strategy_e_analysis.reason == "SIGNAL_READY"
    # Passive diagnostics must not consume the execution evaluator's bar.
    assert service.strategy_e.last_processed_candle is None


def test_strategy_e_diagnostics_separate_analysis_from_closed_entry_window():
    service = StrategyService(oms_service=Mock(), repository=Mock())
    service.config.mode = AutoTradingMode.PAPER
    service.config.tunables.pivot_vwap_scalp_enabled = True
    service._strategy_e_futures_5m = [
        _candle(24, 5, open_=101.0, high=102.0, low=100.5, close=101.5)
    ]
    service._market_data_status["strategy_e_signal_data_fresh"] = True
    service._strategy_e_analysis = StrategyEDecision(
        "NO_TRADE",
        "NO_SETUP",
        {"price": 101.5, "pivot": 100.0},
    )
    service._strategy_e_entry_window_open = Mock(return_value=False)

    [diag] = service._strategy_e_trigger_diagnostics()
    conditions = {condition.id: condition for condition in diag.conditions}

    assert conditions["entry_window"].current_value == "CLOSED"
    assert conditions["entry_window"].status == "PENDING"
    assert conditions["latest_setup"].current_value == "NO_TRADE"
    assert conditions["latest_setup"].gap_description == "NO_SETUP"
    assert diag.key_blocker == "NO_SETUP"
    assert diag.overall_status == "WAITING"
