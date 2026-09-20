from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from libs.contracts.models import Candle
from services.strategy.contract_selector import ContractSelector, trading_sessions_remaining
from services.strategy.futures_signal import FuturesContractResolver, FuturesFeatureEngine, aggregate_completed_15m
from services.strategy.models import (
    OptionSelectionConfig,
    SetupInvalidationState,
    StrategyDirection,
    StrategySetup,
    StrategyState,
    StrategyStateSnapshot,
    StrategyTunablesConfig,
)
from services.strategy.option_execution_validation import ForwardOptionExecutionValidator
from services.strategy.position_manager import UnderlyingRiskSizer
from services.strategy.replay_strategy_a import StrategyAReplayEngine, compare_replay_decisions
from services.strategy.replay_metadata import build_configuration_snapshot
from services.strategy.models import AutoTradingConfig, HistoricalReplaySource, SessionTimersConfig, ThresholdOverrides
from services.strategy.service import StrategyService
from unittest.mock import Mock


UTC = timezone.utc


def candle(ts: datetime, close: float, *, instrument: str = "INST-NIFTY-FUT-2026-09-24", interval: str = "15m", high: float | None = None, low: float | None = None, volume: int = 100) -> Candle:
    return Candle(instrument_id=instrument, interval=interval, start_time=ts, end_time=ts + timedelta(minutes=15 if interval == "15m" else 5), open=close - 1, high=high or close + 2, low=low or close - 2, close=close, volume=volume, source="BREEZE")


def setup() -> StrategySetup:
    ts = datetime(2026, 9, 20, 9, 45, tzinfo=UTC)
    return StrategySetup(direction=StrategyDirection.CALL, setup_timestamp=ts, confirmation_bar_timestamp=ts, confirmation_high=110, confirmation_low=100, trigger_price=110, structural_stop=100, initial_underlying_r=10, relevant_support_resistance_level=100, confluence_references=("CONFIRMED_SR",), setup_expiry_timestamp=ts + timedelta(minutes=30), setup_expiry_bar_index=2)


def test_strategy_contracts_are_frozen_and_entered_consumes_setup():
    snapshot = StrategyStateSnapshot().transition(StrategyState.SETUP, setup=setup()).transition(StrategyState.ARMED).transition(StrategyState.ENTERED, entry_timestamp=setup().setup_timestamp + timedelta(minutes=15))
    assert snapshot.setup.invalidation_state is SetupInvalidationState.CONSUMED
    with pytest.raises((TypeError, ValidationError)):
        snapshot.setup.trigger_price = 1  # type: ignore[misc]


def test_future_bars_cannot_change_as_of_features_or_pivots():
    start = datetime(2026, 9, 20, 9, 15, tzinfo=UTC)
    bars = [candle(start + timedelta(minutes=15 * i), 100 + i) for i in range(55)]
    at = bars[-1].end_time
    before = FuturesFeatureEngine.build(bars, as_of=at)
    after = FuturesFeatureEngine.build(bars + [candle(start + timedelta(minutes=15 * 55), 10)], as_of=at)
    assert before == after


def test_aggregation_requires_three_contiguous_completed_futures_bars():
    start = datetime(2026, 9, 20, 9, 15, tzinfo=UTC)
    bars = [candle(start + timedelta(minutes=5 * i), 100 + i, interval="5m") for i in range(3)]
    assert len(aggregate_completed_15m(bars, as_of=bars[-1].end_time)) == 1
    assert len(aggregate_completed_15m(bars[:2], as_of=bars[1].end_time)) == 0


def test_contract_resolver_never_splices_futures_contracts():
    ts = datetime(2026, 9, 20, 9, 15, tzinfo=UTC)
    old = candle(ts, 100, instrument="INST-NIFTY-FUT-2026-09-17")
    new = candle(ts, 101, instrument="INST-NIFTY-FUT-2026-09-24")
    assert FuturesContractResolver().resolve([old, new], as_of=ts) == "INST-NIFTY-FUT-2026-09-24"


def test_option_selector_requires_delta_and_ranks_inside_preferred_band():
    as_of = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    chain = {"timestamp": as_of.isoformat(), "strikes": [
        {"strike": 24000, "call": {"expiry": "2026-09-24", "instrument_id": "A", "bid": 100, "ask": 101, "delta": 0.55, "open_interest": 20000, "volume": 1000, "lot_size": 75}},
        {"strike": 24100, "call": {"expiry": "2026-09-24", "instrument_id": "B", "bid": 80, "ask": 81, "delta": 0.625, "open_interest": 10000, "volume": 1000, "lot_size": 75}},
        {"strike": 24200, "call": {"expiry": "2026-09-24", "instrument_id": "C", "bid": 60, "ask": 61, "open_interest": 50000, "volume": 1000, "lot_size": 75}},
    ]}
    selected, inspected, reason = ContractSelector().select_contract(direction=__import__("services.strategy.models", fromlist=["TradeDirection"]).TradeDirection.BULLISH, underlying_price=24000, option_chain=chain, as_of=as_of)
    assert reason is None
    assert selected.instrument_id == "B"
    assert selected.greek_source == "BROKER"
    assert any(row["status"].startswith("REJECTED_DELTA") for row in inspected)


def test_expiry_requires_two_trading_sessions_not_two_calendar_days():
    assert trading_sessions_remaining(datetime(2026, 9, 18).date(), datetime(2026, 9, 21).date()) == 1
    assert trading_sessions_remaining(datetime(2026, 9, 17).date(), datetime(2026, 9, 21).date()) == 2


def test_structural_r_sizing_rejects_missing_delta_without_fallback():
    sizer = UnderlyingRiskSizer()
    with pytest.raises(ValueError, match="OPTION_RISK_UNAVAILABLE"):
        sizer.size(underlying_entry=100, underlying_stop=90, option_delta=None, lot_size=75, option_entry=100)
    estimate = sizer.size(underlying_entry=100, underlying_stop=90, option_delta=0.625, lot_size=75, option_entry=100)
    assert estimate.method == "DELTA_APPROXIMATION"
    assert estimate.underlying_r == 10


def test_replay_report_is_versioned_and_comparison_is_event_level():
    start = datetime(2026, 9, 20, 9, 15, tzinfo=UTC)
    report = StrategyAReplayEngine().replay([candle(start + timedelta(minutes=15 * i), 100 + i) for i in range(55)])
    assert report.strategy_version == "trend_pullback_confluence_v1"
    assert report.futures_contracts == ["INST-NIFTY-FUT-2026-09-24"]
    assert compare_replay_decisions(report.decisions, report.decisions) == []


def test_forward_option_validation_keeps_underlying_and_option_states_separate():
    assert ForwardOptionExecutionValidator().validate([], {}).total_underlying_signals == 0


def test_legacy_configuration_sentinel_propagates_without_hidden_defaults():
    config = StrategyTunablesConfig(legacy_strategy_a_adx_threshold=27.0, legacy_trigger_buffer_atr=0.031, legacy_min_impulse_atr=0.83, legacy_retest_tolerance_atr=0.37, legacy_min_available_confirmations=4)
    service = StrategyService(oms_service=Mock(), repository=Mock())
    service.config = AutoTradingConfig(tunables=config)
    service._sync_subcomponents()
    assert service.strategy_a.config.adx_threshold == config.adx_threshold
    assert service.simulation_engine.tunables.legacy_strategy_a_adx_threshold == 27.0
    metadata = build_configuration_snapshot(start_date="2026-09-20", end_date="2026-09-20", instrument_id="INST-NIFTY-FUT-2026-09-24", historical_source=HistoricalReplaySource.BREEZE, bypass_entry_window=False, strategy_a_enabled=True, overrides=ThresholdOverrides(), tunables=config, session=SessionTimersConfig())
    assert metadata.strategy_a["compatibility_config"]["legacy_fields"]["adx_threshold"] == 27.0
