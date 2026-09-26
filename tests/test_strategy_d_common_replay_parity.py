from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from libs.contracts.models import Candle
from services.historical.strategy_d_candidate_manifest import (
    CANDIDATE_ID,
    spec_fingerprint,
)
from services.historical.strategy_d_sr_momentum_backtest import run_backtest
from services.strategy.models import (
    MarketFeatures,
    RiskConfig,
    SessionTimersConfig,
    StrategyName,
    StrategyTunablesConfig,
    ThresholdOverrides,
)
from services.strategy.replay_lifecycle import HistoricalPositionManagerReplayer
from services.strategy.replay_manifest import ReplayManifestRecorder
from services.strategy.replay_registry import (
    ReplayBarContext,
    ReplaySessionContext,
    ReplayStrategyRegistry,
)
from services.strategy.strategies.sr_momentum_breakout import (
    PivotLevels,
    StrategyDConfig,
    StrategyDSignal,
    TradeDirection,
)

IST = ZoneInfo("Asia/Kolkata")


def _bar(day, hour, minute, close, *, instrument_id="INST-NIFTY-INDEX"):
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    return Candle(
        instrument_id=instrument_id,
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=100,
        open_interest=1000,
        source="BREEZE",
    )


def test_strategy_d_dedicated_backtest_and_common_replay_have_same_v2_trade(monkeypatch):
    previous = date(2026, 9, 23)
    current = date(2026, 9, 24)
    entry = _bar(current, 10, 0, 100.0)
    stop = _bar(current, 10, 5, 94.0)
    previous_bars = [
        _bar(previous, 9, 15, 90.0),
        _bar(previous, 15, 25, 100.0),
    ]
    session_bars = [entry, stop]
    future_entry = _bar(
        current, 10, 0, 101.0,
        instrument_id="INST-NIFTY-FUT-2026-09-29",
    )
    future_stop = _bar(
        current, 10, 5, 101.0,
        instrument_id="INST-NIFTY-FUT-2026-09-29",
    )
    futures = [future_entry, future_stop]
    levels = PivotLevels(
        session_date=current,
        source_session_date=previous,
        pdh=100.0,
        pdl=90.0,
        pdc=100.0,
        pivot=96.666667,
        r1=103.333334,
        s1=93.333334,
        r2=106.666667,
        s2=86.666667,
    )
    raw_signal = StrategyDSignal(
        strategy_id=StrategyDConfig.v2_candidate().strategy_id,
        direction=TradeDirection.BULLISH,
        option_type="CALL",
        timestamp=entry.end_time,
        breakout_level_name="PDH",
        breakout_level=100.0,
        entry_price=100.0,
        initial_stop=95.0,
        risk_points=5.0,
        atr_5m=3.333333,
        rsi_previous=59.0,
        rsi_current=63.0,
        rsi_clearance_points=3.0,
        previous_day_range_atr=3.0,
        vwap_reference_price=101.0,
        vwap=99.0,
        vwap_source="ACTIVE_NIFTY_FUTURES_5M",
        next_pivot_name="R2",
        next_pivot_price=106.666667,
        levels=levels,
    )

    def shared_signal_evaluator(spot_history, futures_history, levels_arg, config):
        assert config.variant == "V2_CANDIDATE"
        return raw_signal if spot_history[-1].end_time == entry.end_time else None

    monkeypatch.setattr(
        "services.historical.strategy_d_sr_momentum_backtest.evaluate_strategy_d_signal",
        shared_signal_evaluator,
    )
    monkeypatch.setattr(
        "services.strategy.replay_registry.evaluate_strategy_d_signal",
        shared_signal_evaluator,
    )
    monkeypatch.setattr(
        "services.strategy.replay_registry.previous_session_levels",
        lambda *args, **kwargs: levels,
    )

    dedicated = run_backtest(
        spot_candles=previous_bars + session_bars,
        futures_candles=futures,
        start_date=current,
        end_date=current,
        config=StrategyDConfig.v2_candidate(),
    )
    assert len(dedicated["trades"]) == 1
    dedicated_trade = dedicated["trades"][0]

    recorder = ReplayManifestRecorder()
    registry = ReplayStrategyRegistry.default(
        StrategyTunablesConfig(),
        SessionTimersConfig(),
        selected_strategies=[StrategyName.SR_MOMENTUM_BREAKOUT],
    )
    session = ReplaySessionContext(
        trading_date=current.isoformat(),
        instrument_id="INST-NIFTY-INDEX",
        overrides=ThresholdOverrides(),
        recorder=recorder,
    )
    registry.prepare_session(session)
    running = list(previous_bars)
    record = None
    for bar in session_bars:
        running.append(bar)
        active_futures = [item for item in futures if item.end_time <= bar.end_time]
        context = ReplayBarContext(
            session=session,
            bar=bar,
            features=MarketFeatures(timestamp=bar.end_time, spot_price=bar.close),
            spot_candles_5m=running,
            spot_candles_15m=[],
            futures_candles=active_futures,
            active_futures_candles_5m=active_futures,
        )
        evaluations = registry.evaluate_completed_bar(context, allow_evaluation=True)
        signal = registry.first_signal(evaluations)
        if signal is not None:
            record = registry.confirm_entry(signal, context)
            break

    assert record is not None
    replayer = HistoricalPositionManagerReplayer(
        risk_config=RiskConfig(),
        session_config=SessionTimersConfig(),
        recorder=recorder,
        instrument_id="INST-NIFTY-INDEX",
        warmup_candles=previous_bars,
        session_candles=session_bars,
        futures_candles=futures,
        one_minute_candles=[],
    )
    position = replayer.start_record(record)
    assert position is not None
    assert replayer.advance_record(position, stop, running + [stop]) is False

    assert record.lifecycle_status == "RESOLVED"
    assert record.exit_timestamp == datetime.fromisoformat(dedicated_trade["exit_time"])
    assert record.exit_price == dedicated_trade["runner_exit_price"]
    assert record.exit_reason == dedicated_trade["runner_exit_reason"]
    assert record.realized_r == dedicated_trade["realized_r"]
    assert record.initial_risk_points == dedicated_trade["risk_points"]


def test_strategy_d_registry_keeps_v1_control_and_frozen_v2_explicit():
    registry = ReplayStrategyRegistry.default(
        StrategyTunablesConfig(),
        SessionTimersConfig(),
        selected_strategies=[StrategyName.SR_MOMENTUM_BREAKOUT],
    )
    metadata = registry.strategy_metadata()
    assert len(metadata) == 1
    assert metadata[0].display_name == "Strategy D · S&R Momentum V2 (Frozen Candidate)"
    assert StrategyDConfig.v1_control().variant == "V1_CONTROL"
    assert StrategyDConfig.v2_candidate().variant == "V2_CANDIDATE"
    assert StrategyDConfig.v1_control().strategy_id.endswith("_V1")
    assert StrategyDConfig.v2_candidate().strategy_id == CANDIDATE_ID
    assert len(spec_fingerprint()) == 64


def test_strategy_d_natural_signal_discovery_matches_dedicated_and_common_replay():
    """Parity must include D's real pivot/RSI/ATR/VWAP discovery, not a stub."""
    previous = date(2026, 9, 23)
    current = date(2026, 9, 24)
    closes = [
        95.5, 97.5, 95.5, 97.5, 96.5, 98.5, 96.5, 95.5,
        97.5, 98.5, 96.5, 97.5, 99.5, 97.5, 99.5, 102.0,
    ]

    def candle(day, hour, minute, close, *, high=None, low=None, instrument="INST-NIFTY-INDEX"):
        start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
        return Candle(
            instrument_id=instrument,
            interval="5m",
            start_time=start,
            end_time=start + timedelta(minutes=5),
            open=close,
            high=float(high if high is not None else close + 1.0),
            low=float(low if low is not None else close - 1.0),
            close=float(close),
            volume=1000,
            open_interest=10000,
            source="BREEZE",
        )

    # These two bars establish PDH=100 / PDL=90 while also forming the first
    # two observations in the real RSI/ATR history used by both paths.
    previous_bars = [
        candle(previous, 9, 15, closes[0], high=100.0, low=90.0),
        candle(previous, 15, 25, closes[1], high=99.0, low=94.0),
    ]
    session_bars = []
    futures = []
    start = datetime(current.year, current.month, current.day, 9, 15, tzinfo=IST)
    for index, close in enumerate(closes[2:]):
        at = start + timedelta(minutes=5 * index)
        session_bars.append(candle(current, at.hour, at.minute, close))
        # A rising futures series gives the shared VWAP authority an
        # unambiguous bullish confirmation on the breakout bar.
        futures.append(candle(
            current,
            at.hour,
            at.minute,
            200.0 + index,
            instrument="INST-NIFTY-FUT-2026-09-29",
        ))
    # A later adverse bar resolves the naturally discovered trade.
    adverse_at = start + timedelta(minutes=5 * len(closes[2:]))
    adverse = candle(current, adverse_at.hour, adverse_at.minute, 90.0)
    session_bars.append(adverse)
    futures.append(candle(
        current,
        adverse_at.hour,
        adverse_at.minute,
        215.0,
        instrument="INST-NIFTY-FUT-2026-09-29",
    ))

    cfg = StrategyDConfig.v2_candidate()
    dedicated = run_backtest(
        spot_candles=previous_bars + session_bars,
        futures_candles=futures,
        start_date=current,
        end_date=current,
        config=cfg,
    )
    assert len(dedicated["trades"]) == 1
    dedicated_trade = dedicated["trades"][0]
    assert dedicated_trade["rsi_previous"] <= cfg.long_rsi_cross
    assert dedicated_trade["rsi_current"] > (
        cfg.long_rsi_cross + cfg.minimum_rsi_clearance_points
    )
    assert dedicated_trade["breakout_level_name"] == "PDH"

    recorder = ReplayManifestRecorder()
    registry = ReplayStrategyRegistry.default(
        StrategyTunablesConfig(),
        SessionTimersConfig(),
        selected_strategies=[StrategyName.SR_MOMENTUM_BREAKOUT],
    )
    replay_session = ReplaySessionContext(
        trading_date=current.isoformat(),
        instrument_id="INST-NIFTY-INDEX",
        overrides=ThresholdOverrides(),
        recorder=recorder,
    )
    registry.prepare_session(replay_session)
    running = list(previous_bars)
    record = None
    entry_index = None
    for index, bar in enumerate(session_bars):
        running.append(bar)
        active_futures = [item for item in futures if item.end_time <= bar.end_time]
        context = ReplayBarContext(
            session=replay_session,
            bar=bar,
            features=MarketFeatures(timestamp=bar.end_time, spot_price=bar.close),
            spot_candles_5m=list(running),
            spot_candles_15m=[],
            futures_candles=active_futures,
            active_futures_candles_5m=active_futures,
        )
        signal = registry.first_signal(
            registry.evaluate_completed_bar(context, allow_evaluation=True)
        )
        if signal is not None:
            record = registry.confirm_entry(signal, context)
            entry_index = index
            break

    assert record is not None
    assert entry_index is not None
    assert record.simulated_entry_timestamp.isoformat() == dedicated_trade["entry_time"]
    assert record.initial_structural_stop == dedicated_trade["initial_stop"]
    assert record.initial_risk_points == dedicated_trade["risk_points"]

    replayer = HistoricalPositionManagerReplayer(
        risk_config=RiskConfig(),
        session_config=SessionTimersConfig(),
        recorder=recorder,
        instrument_id="INST-NIFTY-INDEX",
        warmup_candles=previous_bars,
        session_candles=session_bars,
        futures_candles=futures,
        one_minute_candles=[],
    )
    position = replayer.start_record(record)
    assert position is not None
    running_for_lifecycle = previous_bars + session_bars[: entry_index + 1]
    for bar in session_bars[entry_index + 1 :]:
        running_for_lifecycle.append(bar)
        if not replayer.advance_record(position, bar, running_for_lifecycle):
            break

    assert record.lifecycle_status == "RESOLVED"
    assert record.exit_timestamp == datetime.fromisoformat(dedicated_trade["exit_time"])
    assert record.exit_price == dedicated_trade["runner_exit_price"]
    assert record.exit_reason == dedicated_trade["runner_exit_reason"]
    assert record.realized_r == dedicated_trade["realized_r"]
