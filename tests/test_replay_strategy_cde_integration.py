from datetime import datetime, timedelta, timezone

from libs.contracts.models import Candle
from services.strategy.models import (
    HistoricalReplaySource,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    StrategyName,
    StrategySignal,
    StrategyTunablesConfig,
    ThresholdOverrides,
    TradeDirection,
)
from services.strategy.replay_lifecycle import HistoricalPositionManagerReplayer
from services.strategy.replay_manifest import ReplayManifestRecorder
from services.strategy.replay_metadata import (
    build_configuration_snapshot,
    build_data_fingerprint,
    configuration_fingerprint,
)
from services.strategy.replay_registry import (
    DiContinuationReplayAdapter,
    SRMomentumBreakoutReplayAdapter,
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
from services.strategy.strategies.sr_momentum_breakout import (
    PivotLevels,
    StrategyDSignal,
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


def test_strategy_d_holds_fresh_signal_and_freezes_level_when_observe_only(
    monkeypatch,
):
    day = datetime(2026, 9, 24, 10, 0, tzinfo=IST)
    levels = PivotLevels(
        session_date=day.date(),
        source_session_date=(day - timedelta(days=1)).date(),
        pdh=100.0,
        pdl=90.0,
        pdc=95.0,
        pivot=95.0,
        r1=100.0,
        s1=90.0,
        r2=105.0,
        s2=85.0,
    )
    raw_signal = StrategyDSignal(
        strategy_id="STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE",
        direction=TradeDirection.BULLISH,
        option_type="CALL",
        timestamp=day.astimezone(UTC),
        breakout_level_name="PDH",
        breakout_level=100.0,
        entry_price=100.0,
        initial_stop=95.0,
        risk_points=5.0,
        atr_5m=3.0,
        rsi_previous=59.0,
        rsi_current=63.0,
        rsi_clearance_points=3.0,
        previous_day_range_atr=3.0,
        vwap_reference_price=101.0,
        vwap=99.0,
        vwap_source="ACTIVE_NIFTY_FUTURES_5M",
        next_pivot_name="R2",
        next_pivot_price=105.0,
        levels=levels,
    )
    monkeypatch.setattr(
        "services.strategy.replay_registry.previous_session_levels",
        lambda *args, **kwargs: levels,
    )
    calls = {"count": 0}

    def fake_evaluate(*args, **kwargs):
        calls["count"] += 1
        return raw_signal if calls["count"] == 1 else None

    monkeypatch.setattr(
        "services.strategy.replay_registry.evaluate_strategy_d_signal",
        fake_evaluate,
    )

    adapter = SRMomentumBreakoutReplayAdapter(StrategyTunablesConfig())
    recorder = ReplayManifestRecorder()
    session = ReplaySessionContext(
        trading_date="2026-09-24",
        instrument_id="INST-NIFTY-INDEX",
        overrides=ThresholdOverrides(),
        recorder=recorder,
    )
    bar = _bar(day)
    future = _bar(
        day,
        instrument_id="INST-NIFTY-FUT-2026-09-29",
    )
    context = ReplayBarContext(
        session=session,
        bar=bar,
        features=__import__(
            "services.strategy.models",
            fromlist=["MarketFeatures"],
        ).MarketFeatures(timestamp=bar.end_time, spot_price=bar.close),
        spot_candles_5m=[bar],
        spot_candles_15m=[],
        futures_candles=[future],
        active_futures_candles_5m=[future],
    )
    adapter.prepare_session(session)

    observed = adapter.evaluate_completed_bar(
        context,
        allow_evaluation=False,
    )

    assert observed.signal is None
    assert observed.phase == "SIGNAL_HELD_BY_HIGHER_PRIORITY_OR_RISK_GATE"
    assert adapter._pending_signal is not None
    assert len(adapter._used_level_keys) == 1

    next_bar = _bar(day + timedelta(minutes=5))
    next_future = _bar(
        day + timedelta(minutes=5),
        instrument_id=future.instrument_id,
    )
    next_context = ReplayBarContext(
        session=session,
        bar=next_bar,
        features=__import__(
            "services.strategy.models",
            fromlist=["MarketFeatures"],
        ).MarketFeatures(
            timestamp=next_bar.end_time,
            spot_price=next_bar.close,
        ),
        spot_candles_5m=[bar, next_bar],
        spot_candles_15m=[],
        futures_candles=[future, next_future],
        active_futures_candles_5m=[future, next_future],
    )
    released = adapter.evaluate_completed_bar(
        next_context,
        allow_evaluation=True,
    )

    assert released.signal is not None
    assert released.signal.signal_id == strategy_d_signal_id(raw_signal)
    adapter.on_execution_rejected(released.signal, "TEST_REJECT")
    assert adapter._pending_signal is None
    assert len(adapter._used_level_keys) == 1


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


def test_native_one_minute_evidence_changes_dataset_fingerprint():
    end = datetime(2026, 9, 24, 10, 0, tzinfo=IST)
    spot_5m = _bar(end)
    future_5m = _bar(
        end,
        instrument_id="INST-NIFTY-FUT-2026-09-29",
    )
    future_1m = _bar(
        end,
        instrument_id=future_5m.instrument_id,
        interval="1m",
    )
    kwargs = {
        "source": HistoricalReplaySource.BREEZE,
        "start_date": "2026-09-24",
        "end_date": "2026-09-24",
        "spot_candles": [spot_5m],
        "futures_candles": [future_5m],
        "source_diagnostics": {},
        "futures_contracts": [{
            "instrument_id": future_5m.instrument_id,
            "expiry": "2026-09-29",
        }],
        "missing_data": [],
    }

    without_1m = build_data_fingerprint(**kwargs)
    with_1m = build_data_fingerprint(
        **kwargs,
        futures_one_minute_candles=[future_1m],
    )

    assert without_1m.futures_one_minute_candle_count == 0
    assert with_1m.futures_one_minute_candle_count == 1
    assert without_1m.dataset_hash != with_1m.dataset_hash


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


def _record_signal(
    recorder: ReplayManifestRecorder,
    signal: StrategySignal,
    entry_bar: Candle,
    *,
    entry_features: dict | None = None,
):
    entry = float(
        signal.underlying_entry_price or signal.spot_reference_price
    )
    risk = float(signal.r_points)
    return recorder.record_entry(
        signal=signal,
        trading_date=entry_bar.end_time.astimezone(IST).date().isoformat(),
        trigger_source_candle_timestamp=signal.timestamp,
        trigger_level=entry,
        simulated_entry_timestamp=signal.timestamp,
        simulated_entry_price=entry,
        entry_5m_candle_timestamp=entry_bar.end_time,
        entry_occurred_intrabar=False,
        entry_features=entry_features or signal.features_snapshot,
        setup_id=signal.signal_id,
        pullback_swing_low=None,
        pullback_swing_high=None,
        impulse_low=None,
        impulse_high=None,
        atr_at_entry=risk,
        initial_structural_stop=float(signal.structural_stop),
        initial_risk_points=risk,
        initial_risk_atr=1.0,
        current_trailing_stop=float(signal.structural_stop),
        current_r=0.0,
        highest_favorable_price=entry,
        lowest_favorable_price=entry,
        peak_r=0.0,
        protected_breakeven_active=False,
        profit_lock_active=False,
        runner_mode_active=False,
        current_ladder_stage="OPEN_INITIAL_RISK",
        reversal_score=0,
        adverse_health_counters={},
        entry_bar_timestamp=entry_bar.end_time,
        last_managed_completed_bar_timestamp=None,
    )


def test_strategy_c_lifecycle_dispatch_uses_frozen_observer(monkeypatch):
    entry_end = datetime(2026, 9, 24, 10, 0, tzinfo=IST)
    entry_bar = _bar(
        entry_end,
        instrument_id="INST-NIFTY-FUT-2026-09-29",
    )
    minute = _bar(
        entry_end,
        instrument_id=entry_bar.instrument_id,
        interval="1m",
    )
    signal = StrategySignal(
        signal_id="C-LIFECYCLE",
        strategy=StrategyName.DI_CONTINUATION,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        timestamp=entry_end.astimezone(UTC) - timedelta(minutes=2),
        spot_reference_price=100.0,
        underlying_entry_price=100.0,
        structural_stop=95.0,
        r_points=5.0,
        derivatives_score=0.0,
    )
    recorder = ReplayManifestRecorder()
    record = _record_signal(
        recorder,
        signal,
        entry_bar,
        entry_features={
            "underlying_entry_price": 100.0,
            "futures_contract": entry_bar.instrument_id,
        },
    )
    monkeypatch.setattr(
        "services.strategy.replay_lifecycle.replay_strategy_c_to_as_of",
        lambda *args, **kwargs: {
            "candidate_entries": [{
                "candidate_signal_id": signal.signal_id,
                "lifecycle": {
                    "status": "RESOLVED",
                    "current_stop": 95.0,
                    "current_r": -1.0,
                    "exit_time": (
                        signal.timestamp + timedelta(minutes=1)
                    ).isoformat(),
                    "exit_price": 95.0,
                    "exit_reason": "STOP_OR_TRAIL",
                    "realized_r": -1.0,
                    "mfe_r": 0.1,
                    "mae_r": -1.0,
                },
            }],
        },
    )
    replayer = HistoricalPositionManagerReplayer(
        risk_config=RiskConfig(),
        session_config=SessionTimersConfig(),
        recorder=recorder,
        instrument_id="INST-NIFTY-INDEX",
        warmup_candles=[],
        session_candles=[entry_bar],
        futures_candles=[entry_bar],
        one_minute_candles=[],
        futures_one_minute_candles=[minute],
    )

    position = replayer.start_record(record)

    assert position is None
    assert record.lifecycle_status == "RESOLVED"
    assert record.exit_reason == "STOP_OR_TRAIL"
    assert record.realized_r == -1.0
    assert replayer.stats["strategy_c_resolved"] == 1


def test_strategy_d_lifecycle_dispatch_uses_frozen_v2_manager():
    entry_end = datetime(2026, 9, 24, 10, 0, tzinfo=IST)
    entry_bar = _bar(
        entry_end,
        open_=100.0,
        high=101.0,
        low=99.5,
        close=100.0,
    )
    stop_bar = _bar(
        entry_end + timedelta(minutes=5),
        open_=100.0,
        high=100.5,
        low=94.0,
        close=95.0,
    )
    levels = {
        "session_date": "2026-09-24",
        "source_session_date": "2026-09-23",
        "pdh": 100.0,
        "pdl": 90.0,
        "pdc": 95.0,
        "pivot": 95.0,
        "r1": 100.0,
        "s1": 90.0,
        "r2": 105.0,
        "s2": 85.0,
    }
    raw = {
        "strategy_id": "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE",
        "direction": "BULLISH",
        "option_type": "CALL",
        "timestamp": entry_end.astimezone(UTC).isoformat(),
        "breakout_level_name": "PDH",
        "breakout_level": 100.0,
        "entry_price": 100.0,
        "initial_stop": 95.0,
        "risk_points": 5.0,
        "atr_5m": 3.333333,
        "rsi_previous": 59.0,
        "rsi_current": 63.0,
        "rsi_clearance_points": 3.0,
        "previous_day_range_atr": 3.0,
        "vwap_reference_price": 101.0,
        "vwap": 99.0,
        "vwap_source": "ACTIVE_NIFTY_FUTURES_5M",
        "next_pivot_name": "R2",
        "next_pivot_price": 105.0,
        "levels": levels,
    }
    signal = StrategySignal(
        signal_id=strategy_d_signal_id(raw),
        strategy=StrategyName.SR_MOMENTUM_BREAKOUT,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        timestamp=entry_end.astimezone(UTC),
        spot_reference_price=100.0,
        underlying_entry_price=100.0,
        structural_stop=95.0,
        r_points=5.0,
        derivatives_score=0.0,
        features_snapshot={"strategy_d_signal": raw},
    )
    recorder = ReplayManifestRecorder()
    record = _record_signal(
        recorder,
        signal,
        entry_bar,
        entry_features={
            "strategy_d_signal": raw,
            "underlying_entry_price": 100.0,
        },
    )
    replayer = HistoricalPositionManagerReplayer(
        risk_config=RiskConfig(),
        session_config=SessionTimersConfig(),
        recorder=recorder,
        instrument_id="INST-NIFTY-INDEX",
        warmup_candles=[],
        session_candles=[entry_bar, stop_bar],
        futures_candles=[],
        one_minute_candles=[],
    )
    position = replayer.start_record(record)
    assert position is not None

    active = replayer.advance_record(
        position,
        stop_bar,
        [entry_bar, stop_bar],
    )

    assert active is False
    assert record.lifecycle_status == "RESOLVED"
    assert record.exit_reason == "ATR_HARD_STOP"
    assert record.realized_r == -1.0
    assert replayer.stats["strategy_d_resolved"] == 1


def test_strategy_e_lifecycle_dispatch_uses_shared_production_helper():
    entry_end = datetime(2026, 9, 24, 10, 0, tzinfo=IST)
    entry_spot = _bar(entry_end)
    next_spot = _bar(entry_end + timedelta(minutes=5))
    futures_id = "INST-NIFTY-FUT-2026-09-29"
    entry_future = _bar(
        entry_end,
        instrument_id=futures_id,
        open_=100.0,
        high=102.0,
        low=99.0,
        close=100.0,
    )
    ambiguous_future = _bar(
        entry_end + timedelta(minutes=5),
        instrument_id=futures_id,
        open_=100.0,
        high=111.0,
        low=94.0,
        close=108.0,
    )
    signal = StrategySignal(
        signal_id="E-LIFECYCLE",
        strategy=StrategyName.PIVOT_VWAP_SCALP,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        timestamp=entry_end.astimezone(UTC),
        spot_reference_price=100.0,
        underlying_entry_price=100.0,
        structural_stop=95.0,
        r_points=5.0,
        derivatives_score=0.0,
        features_snapshot={
            "target_price": 110.0,
            "strategy_target_price": 110.0,
            "futures_contract": futures_id,
            "signal_type": "TREND_CONTINUATION",
        },
    )
    recorder = ReplayManifestRecorder()
    record = _record_signal(
        recorder,
        signal,
        entry_spot,
        entry_features=signal.features_snapshot,
    )
    replayer = HistoricalPositionManagerReplayer(
        risk_config=RiskConfig(),
        session_config=SessionTimersConfig(),
        recorder=recorder,
        instrument_id="INST-NIFTY-INDEX",
        warmup_candles=[],
        session_candles=[entry_spot, next_spot],
        futures_candles=[entry_future, ambiguous_future],
        one_minute_candles=[],
    )
    position = replayer.start_record(record)
    assert position is not None

    active = replayer.advance_record(
        position,
        next_spot,
        [entry_spot, next_spot],
    )

    assert active is False
    assert record.lifecycle_status == "RESOLVED"
    assert record.exit_reason == "STRATEGY_E_STOP_LOSS"
    assert record.exit_price == 95.0
    assert record.realized_r == -1.0
    assert replayer.stats["strategy_e_resolved"] == 1
