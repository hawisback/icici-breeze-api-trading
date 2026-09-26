from datetime import datetime, timedelta, timezone

from libs.contracts.models import Candle
from services.strategy.models import (
    MarketFeatures,
    SessionTimersConfig,
    StrategyName,
    StrategySignal,
    StrategyTunablesConfig,
    ThresholdOverrides,
    TradeDirection,
)
from services.strategy.replay_manifest import ReplayManifestRecorder
from services.strategy.replay_registry import (
    PivotVwapScalpReplayAdapter,
    ReplayBarContext,
    ReplaySessionContext,
    ReplayStrategyRegistry,
)
from services.strategy.strategies.pivot_vwap_scalp import StrategyEDecision

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def _bar(end):
    return Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        interval="5m",
        start_time=end.astimezone(UTC) - timedelta(minutes=5),
        end_time=end.astimezone(UTC),
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1000,
        open_interest=1000,
        source="BREEZE",
    )


def _context(adapter):
    end = datetime(2026, 9, 24, 10, 0, tzinfo=IST)
    bar = _bar(end)
    recorder = ReplayManifestRecorder()
    session = ReplaySessionContext(
        trading_date="2026-09-24",
        instrument_id="INST-NIFTY-INDEX",
        overrides=ThresholdOverrides(),
        recorder=recorder,
    )
    adapter.prepare_session(session)
    return ReplayBarContext(
        session=session,
        bar=bar,
        features=MarketFeatures(timestamp=bar.end_time, spot_price=bar.close),
        spot_candles_5m=[bar],
        spot_candles_15m=[],
        futures_candles=[bar],
        active_futures_candles_5m=[bar],
    )


def test_strategy_e_is_enabled_for_production_and_common_day_replay_by_default():
    tunables = StrategyTunablesConfig()
    assert tunables.pivot_vwap_scalp_enabled is True

    default_registry = ReplayStrategyRegistry.default(
        tunables,
        SessionTimersConfig(),
    )
    default_e = next(
        item for item in default_registry.strategy_metadata()
        if item.strategy == StrategyName.PIVOT_VWAP_SCALP
    )
    assert default_e.enabled is True

    selected = ReplayStrategyRegistry.default(
        tunables,
        SessionTimersConfig(),
        selected_strategies=[StrategyName.PIVOT_VWAP_SCALP],
    )
    assert [item.strategy for item in selected.strategy_metadata()] == [
        StrategyName.PIVOT_VWAP_SCALP
    ]
    assert selected.strategy_metadata()[0].enabled is True
    assert tunables.pivot_vwap_scalp_enabled is True


def test_strategy_e_replay_diagnostics_expose_setup_family_and_completed_5m_dedupe(monkeypatch):
    adapter = PivotVwapScalpReplayAdapter(
        StrategyTunablesConfig(),
        replay_selected=True,
    )
    context = _context(adapter)
    signal = StrategySignal(
        signal_id="STRATEGY-E-COUNTER_LONG-TEST",
        strategy=StrategyName.PIVOT_VWAP_SCALP,
        direction=TradeDirection.BULLISH,
        option_type="CALL",
        timestamp=context.bar.end_time,
        spot_reference_price=101.0,
        underlying_entry_price=101.0,
        structural_stop=99.0,
        r_points=2.0,
        derivatives_score=0.0,
        features_snapshot={
            "signal_type": "COUNTER_LONG",
            "target_price": 105.0,
            "completed_candle_timestamp": context.bar.end_time.isoformat(),
        },
    )
    calls = {"count": 0}

    def fake_evaluate(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            adapter.strategy.last_processed_candle = context.bar.end_time
            adapter.strategy.last_decision = StrategyEDecision(
                "COUNTER_LONG",
                "SIGNAL_READY",
                {"pivot": 100.0, "vwap": 100.5},
                signal,
            )
            return adapter.strategy.last_decision
        return StrategyEDecision(
            "NO_TRADE",
            "NO_NEW_COMPLETED_5M_BAR",
            adapter.strategy.last_decision.metrics,
        )

    monkeypatch.setattr(adapter.strategy, "evaluate", fake_evaluate)

    first = adapter.evaluate_completed_bar(context, allow_evaluation=True)
    audit = first.audit_records[0]
    assert first.signal is signal
    assert audit["signal_type"] == "COUNTER_LONG"
    assert audit["setup_family"] == "COUNTERTREND"
    assert audit["completed_5m_candle_timestamp"] == context.bar.end_time.isoformat()
    assert audit["dedupe_state"]["last_processed_candle"] == context.bar.end_time.isoformat()
    assert audit["production_enabled"] is False
    assert audit["replay_selected"] is True

    second = adapter.evaluate_completed_bar(context, allow_evaluation=True)
    assert second.signal is None
    assert second.phase == "NO_NEW_COMPLETED_5M_BAR"


def test_all_a_to_e_can_be_selected_through_common_day_testing_registry():
    tunables = StrategyTunablesConfig()
    for strategy in StrategyName:
        registry = ReplayStrategyRegistry.default(
            tunables,
            SessionTimersConfig(),
            selected_strategies=[strategy],
        )
        metadata = registry.strategy_metadata()
        assert len(metadata) == 1
        assert metadata[0].strategy == strategy
        assert metadata[0].enabled is True
