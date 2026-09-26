from datetime import datetime, timedelta, timezone

from libs.contracts.models import Candle
from services.strategy.models import (
    HistoricalReplaySource,
    OptionType,
    SessionTimersConfig,
    StrategyName,
    StrategySignal,
    StrategyTunablesConfig,
    ThresholdOverrides,
    TradeDirection,
)
from services.strategy.replay_manifest import ReplayManifestRecorder
from services.strategy.replay_metadata import (
    build_configuration_snapshot,
    configuration_fingerprint,
)
from services.strategy.replay_registry import (
    DiContinuationReplayAdapter,
    ReplayBarContext,
    ReplaySessionContext,
    ReplayStrategyRegistry,
)
from services.strategy.strategies.candidate_runtime import (
    strategy_d_signal_from_status,
    strategy_d_signal_id,
)
from services.strategy.strategies.pivot_vwap_scalp import (
    evaluate_strategy_e_lifecycle_bar,
)


UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def _bar(
    end_ist: datetime,
    *,
    instrument_id: str = "INST-NIFTY-INDEX",
    interval: str = "5m",
    open_: float = 100.0,
    high: float = 102.0,
    low: float = 99.0,
    close: float = 101.0,
) -> Candle:
    minutes = 1 if interval == "1m" else 5
    end = end_ist.astimezone(UTC)
    return Candle(
        instrument_id=instrument_id,
        interval=interval,
        start_time=end - timedelta(minutes=minutes),
        end_time=end,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000,
        source="BREEZE",
    )


def test_strategy_c_adapter_recovers_fresh_resolved_intrabar_candidate(
    monkeypatch,
):
    observed_end = datetime(2026, 9, 24, 9, 50, tzinfo=IST)
    entry_time = observed_end - timedelta(minutes=3)
    candidate = {
        "candidate_signal_id": "STRAT-C-TEST",
        "direction": "CALL",
        "entry_time": entry_time.astimezone(UTC).isoformat(),
        "entry_price": 24000.0,
        "initial_stop": 23950.0,
        "setup_end": (
            observed_end - timedelta(minutes=5)
        ).astimezone(UTC).isoformat(),
        "research_features": {"setup_atr": 50.0},
        "lifecycle": {
            "status": "RESOLVED",
            "exit_time": (
                entry_time + timedelta(minutes=2)
            ).astimezone(UTC).isoformat(),
            "exit_price": 23950.0,
            "exit_reason": "STOP_OR_TRAIL",
            "realized_r": -1.0,
            "current_stop": 23950.0,
            "current_r": -1.0,
            "mfe_r": 0.2,
            "mae_r": 1.0,
        },
    }

    monkeypatch.setattr(
        "services.strategy.replay_registry.replay_strategy_c_to_as_of",
        lambda *args, **kwargs: {
            "status": "OK",
            "raw_entries": [candidate],
            "candidate_entries": [candidate],
            "active_raw_trade": None,
        },
    )

    tunables = StrategyTunablesConfig()
    adapter = DiContinuationReplayAdapter(tunables)
    recorder = ReplayManifestRecorder()
    session = ReplaySessionContext(
        trading_date="2026-09-24",
        instrument_id="INST-NIFTY-INDEX",
        overrides=ThresholdOverrides(),
        recorder=recorder,
    )
    spot = _bar(observed_end)
    future = _bar(
        observed_end,
        instrument_id="INST-NIFTY-FUT-2026-09-29",
    )
    future_1m = _bar(
        observed_end,
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        interval="1m",
    )
    context = ReplayBarContext(
        session=session,
        bar=spot,
        features=__import__(
            "services.strategy.models",
            fromlist=["MarketFeatures"],
        ).MarketFeatures(
            timestamp=spot.end_time,
            spot_price=spot.close,
        ),
        spot_candles_5m=[spot],
        spot_candles_15m=[],
        futures_candles=[future],
        active_futures_candles_5m=[future],
        futures_candles_1m=[future_1m],
    )

    adapter.prepare_session(session)
    evaluation = adapter.evaluate_completed_bar(
        context,
        allow_evaluation=True,
    )

    assert evaluation.signal is not None
    assert evaluation.signal.strategy == StrategyName.DI_CONTINUATION
    assert evaluation.signal.signal_id == "STRAT-C-TEST"
    assert evaluation.signal.timestamp == entry_time.astimezone(UTC)
    assert (
        evaluation.signal.features_snapshot[
            "replay_observation_latency_seconds"
        ]
        == 180.0
    )
    assert (
        evaluation.signal.features_snapshot["candidate_lifecycle"]["status"]
        == "RESOLVED"
    )

    record = adapter.on_entry_confirmed(evaluation.signal, context)
    assert record.simulated_entry_timestamp == entry_time.astimezone(UTC)
    assert record.entry_features["replay_observation_latency_seconds"] == 180.0
    assert record.initial_risk_points == 50.0


def test_strategy_c_adapter_respects_post_freeze_boundary():
    tunables = StrategyTunablesConfig()
    adapter = DiContinuationReplayAdapter(tunables)
    recorder = ReplayManifestRecorder()
    session = ReplaySessionContext(
        trading_date="2026-09-22",
        instrument_id="INST-NIFTY-INDEX",
        overrides=ThresholdOverrides(),
        recorder=recorder,
    )
    end = datetime(2026, 9, 22, 10, 0, tzinfo=IST)
    spot = _bar(end)
    future = _bar(end, instrument_id="INST-NIFTY-FUT-2026-09-29")
    context = ReplayBarContext(
        session=session,
        bar=spot,
        features=__import__(
            "services.strategy.models",
            fromlist=["MarketFeatures"],
        ).MarketFeatures(timestamp=spot.end_time, spot_price=spot.close),
        spot_candles_5m=[spot],
        spot_candles_15m=[],
        futures_candles=[future],
        active_futures_candles_5m=[future],
        futures_candles_1m=[
            _bar(
                end,
                instrument_id="INST-NIFTY-FUT-2026-09-29",
                interval="1m",
            )
        ],
    )

    evaluation = adapter.evaluate_completed_bar(
        context,
        allow_evaluation=True,
    )

    assert evaluation.signal is None
    assert evaluation.phase == "WAITING_FOR_POST_FREEZE_SESSION"


def test_strategy_d_signal_identity_is_shared_with_runtime_adapter():
    timestamp = datetime(2026, 9, 24, 5, 0, tzinfo=UTC)
    payload = {
        "strategy_id": "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE",
        "direction": "BULLISH",
        "option_type": "CALL",
        "timestamp": timestamp.isoformat(),
        "breakout_level_name": "PDH",
        "breakout_level": 24000.0,
        "entry_price": 24010.0,
        "initial_stop": 23980.0,
        "risk_points": 30.0,
        "atr_5m": 20.0,
        "rsi_previous": 59.0,
        "rsi_current": 63.0,
        "rsi_clearance_points": 3.0,
        "previous_day_range_atr": 4.0,
        "vwap_reference_price": 24020.0,
        "vwap": 24000.0,
        "vwap_source": "ACTIVE_NIFTY_FUTURES_5M",
        "next_pivot_name": "R2",
        "next_pivot_price": 24100.0,
        "levels": {},
    }
    signal_id = strategy_d_signal_id(payload)
    status = {
        "candidate_id": "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE",
        "candidate_spec_fingerprint": "test",
        "execution_signal": payload,
        "execution_signal_id": signal_id,
    }

    signal = strategy_d_signal_from_status(status, as_of=timestamp)

    assert signal is not None
    assert signal.signal_id == signal_id
    assert signal.strategy == StrategyName.SR_MOMENTUM_BREAKOUT
    assert signal.direction == TradeDirection.BULLISH
    assert signal.option_type == OptionType.CALL


def test_strategy_e_completed_bar_stop_wins_same_bar_target_ambiguity():
    bar = _bar(
        datetime(2026, 9, 24, 10, 0, tzinfo=IST),
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        open_=100.0,
        high=112.0,
        low=94.0,
        close=108.0,
    )

    decision = evaluate_strategy_e_lifecycle_bar(
        direction=TradeDirection.BULLISH,
        entry=100.0,
        risk=5.0,
        stop=95.0,
        target=110.0,
        bar=bar,
        forced_exit_time="15:15",
    )

    assert decision.stop_hit is True
    assert decision.target_hit is True
    assert decision.exit_reason == "STRATEGY_E_STOP_LOSS"
    assert decision.decision_price == 95.0


def test_five_strategy_configuration_changes_replay_fingerprint():
    base = StrategyTunablesConfig(pivot_vwap_scalp_enabled=False)
    enabled_e = base.model_copy(
        update={"pivot_vwap_scalp_enabled": True}
    )
    kwargs = {
        "start_date": "2026-09-24",
        "end_date": "2026-09-24",
        "instrument_id": "INST-NIFTY-INDEX",
        "historical_source": HistoricalReplaySource.BREEZE,
        "bypass_entry_window": False,
        "strategy_a_enabled": True,
        "overrides": ThresholdOverrides(),
        "session": SessionTimersConfig(),
    }

    base_snapshot = build_configuration_snapshot(
        tunables=base,
        **kwargs,
    )
    e_snapshot = build_configuration_snapshot(
        tunables=enabled_e,
        **kwargs,
    )

    assert base_snapshot.strategy_suite["enabled"][
        "PIVOT_VWAP_SCALP"
    ] is False
    assert e_snapshot.strategy_suite["enabled"][
        "PIVOT_VWAP_SCALP"
    ] is True
    assert (
        configuration_fingerprint(base_snapshot)
        != configuration_fingerprint(e_snapshot)
    )


def test_default_registry_priority_matches_production_order():
    registry = ReplayStrategyRegistry.default(
        StrategyTunablesConfig(),
        SessionTimersConfig(),
    )
    assert [
        item.strategy for item in registry.strategy_metadata()
    ] == [
        StrategyName.TREND_PULLBACK,
        StrategyName.VOLATILITY_BREAKOUT,
        StrategyName.DI_CONTINUATION,
        StrategyName.SR_MOMENTUM_BREAKOUT,
        StrategyName.PIVOT_VWAP_SCALP,
    ]
