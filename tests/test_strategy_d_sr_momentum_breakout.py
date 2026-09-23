from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from libs.contracts.models import Candle
from services.strategy.features import FeatureEngine
from services.strategy.models import TradeDirection
from services.strategy.strategies.sr_momentum_breakout import (
    PivotLevels,
    StrategyDPositionManager,
    StrategyDSignal,
    classic_pivot_levels,
    evaluate_strategy_d_signal,
    previous_session_levels,
)


IST = ZoneInfo("Asia/Kolkata")


def _bar(
    start: datetime,
    *,
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int = 100,
    instrument_id: str = "INST-NIFTY-INDEX",
) -> Candle:
    open_value = close if open_ is None else open_
    high_value = max(open_value, close) if high is None else high
    low_value = min(open_value, close) if low is None else low
    return Candle(
        instrument_id=instrument_id,
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=open_value,
        high=high_value,
        low=low_value,
        close=close,
        volume=volume,
        open_interest=1000,
        source="BREEZE",
    )


def _levels(day: date) -> PivotLevels:
    return classic_pivot_levels(
        session_date=day,
        source_session_date=day - timedelta(days=1),
        high=110.0,
        low=90.0,
        close=100.0,
    )


def _history(
    day: date,
    previous_close: float,
    current_close: float,
) -> list[Candle]:
    base = datetime(
        day.year,
        day.month,
        day.day,
        9,
        15,
        tzinfo=IST,
    )
    bars = [
        _bar(
            base + timedelta(minutes=5 * index),
            close=100.0,
        )
        for index in range(14)
    ]
    bars.append(
        _bar(
            base + timedelta(minutes=70),
            close=previous_close,
        )
    )
    bars.append(
        _bar(
            base + timedelta(minutes=75),
            close=current_close,
        )
    )
    return bars


def _futures(day: date, close: float) -> list[Candle]:
    base = datetime(
        day.year,
        day.month,
        day.day,
        9,
        15,
        tzinfo=IST,
    )
    return [
        _bar(
            base + timedelta(minutes=5 * index),
            close=close,
            open_=close,
            high=close + 0.5,
            low=close - 0.5,
            volume=100 + index,
            instrument_id="INST-NIFTY-FUT-2026-09-29",
        )
        for index in range(16)
    ]


def _signal(
    direction: TradeDirection = TradeDirection.BULLISH,
) -> StrategyDSignal:
    day = date(2026, 9, 23)
    levels = _levels(day)
    if direction == TradeDirection.BULLISH:
        return StrategyDSignal(
            strategy_id="STRATEGY_D_SR_MOMENTUM_BREAKOUT_V1",
            direction=direction,
            option_type="CALL",
            timestamp=datetime(
                2026,
                9,
                23,
                10,
                0,
                tzinfo=IST,
            ),
            breakout_level_name="PDH",
            breakout_level=110.0,
            entry_price=111.0,
            initial_stop=108.0,
            risk_points=3.0,
            atr_5m=2.0,
            rsi_previous=59.0,
            rsi_current=61.0,
            vwap_reference_price=112.0,
            vwap=110.0,
            vwap_source="ACTIVE_NIFTY_FUTURES_5M",
            next_pivot_name="R2",
            next_pivot_price=120.0,
            levels=levels,
        )
    return StrategyDSignal(
        strategy_id="STRATEGY_D_SR_MOMENTUM_BREAKOUT_V1",
        direction=direction,
        option_type="PUT",
        timestamp=datetime(
            2026,
            9,
            23,
            10,
            0,
            tzinfo=IST,
        ),
        breakout_level_name="PDL",
        breakout_level=90.0,
        entry_price=89.0,
        initial_stop=92.0,
        risk_points=3.0,
        atr_5m=2.0,
        rsi_previous=41.0,
        rsi_current=39.0,
        vwap_reference_price=88.0,
        vwap=90.0,
        vwap_source="ACTIVE_NIFTY_FUTURES_5M",
        next_pivot_name="S2",
        next_pivot_price=80.0,
        levels=levels,
    )


def test_classic_pivots_include_r2_s2():
    levels = classic_pivot_levels(
        session_date=date(2026, 9, 23),
        source_session_date=date(2026, 9, 22),
        high=110,
        low=90,
        close=100,
    )
    assert levels.pivot == 100.0
    assert levels.r1 == 110.0
    assert levels.s1 == 90.0
    assert levels.r2 == 120.0
    assert levels.s2 == 80.0


def test_previous_session_levels_never_use_current_day_high():
    previous_day = date(2026, 9, 22)
    current_day = date(2026, 9, 23)
    candles = [
        _bar(
            datetime(
                2026,
                9,
                22,
                9,
                15,
                tzinfo=IST,
            ),
            close=100,
            high=110,
            low=95,
        ),
        _bar(
            datetime(
                2026,
                9,
                22,
                15,
                25,
                tzinfo=IST,
            ),
            close=105,
            high=108,
            low=100,
        ),
        _bar(
            datetime(
                2026,
                9,
                23,
                9,
                15,
                tzinfo=IST,
            ),
            close=125,
            high=130,
            low=120,
        ),
    ]
    levels = previous_session_levels(
        candles,
        current_day,
    )
    assert levels is not None
    assert levels.source_session_date == previous_day
    assert levels.pdh == 110.0
    assert levels.pdl == 95.0
    assert levels.pdc == 105.0


def test_long_signal_requires_break_rsi_and_vwap(monkeypatch):
    day = date(2026, 9, 23)
    history = _history(
        day,
        previous_close=109.0,
        current_close=111.0,
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_rsi",
        staticmethod(
            lambda closes, period=14: (
                59.0 if closes[-1] == 109.0 else 61.0
            )
        ),
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_atr",
        staticmethod(
            lambda candles, period=14: 2.0
        ),
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_futures_vwap",
        staticmethod(lambda candles: 110.0),
    )

    signal = evaluate_strategy_d_signal(
        history,
        _futures(day, 112.0),
        _levels(day),
    )

    assert signal is not None
    assert signal.option_type == "CALL"
    assert signal.breakout_level_name in {"PDH", "R1"}
    assert signal.initial_stop == 108.0
    assert signal.risk_points == 3.0
    assert signal.next_pivot_name == "R2"
    assert signal.next_pivot_price == 120.0


def test_short_signal_uses_symmetric_atr_stop(monkeypatch):
    day = date(2026, 9, 23)
    history = _history(
        day,
        previous_close=91.0,
        current_close=89.0,
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_rsi",
        staticmethod(
            lambda closes, period=14: (
                41.0 if closes[-1] == 91.0 else 39.0
            )
        ),
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_atr",
        staticmethod(
            lambda candles, period=14: 2.0
        ),
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_futures_vwap",
        staticmethod(lambda candles: 90.0),
    )

    signal = evaluate_strategy_d_signal(
        history,
        _futures(day, 88.0),
        _levels(day),
    )

    assert signal is not None
    assert signal.option_type == "PUT"
    assert signal.initial_stop == 92.0
    assert signal.risk_points == 3.0
    assert signal.next_pivot_name == "S2"


def test_rsi_trap_zone_rejected(monkeypatch):
    day = date(2026, 9, 23)
    history = _history(
        day,
        previous_close=109.0,
        current_close=111.0,
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_rsi",
        staticmethod(
            lambda closes, period=14: 50.0
        ),
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_atr",
        staticmethod(
            lambda candles, period=14: 2.0
        ),
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_futures_vwap",
        staticmethod(lambda candles: 110.0),
    )
    assert (
        evaluate_strategy_d_signal(
            history,
            _futures(day, 112.0),
            _levels(day),
        )
        is None
    )


def test_wrong_side_of_futures_vwap_blocks_long(monkeypatch):
    day = date(2026, 9, 23)
    history = _history(
        day,
        previous_close=109.0,
        current_close=111.0,
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_rsi",
        staticmethod(
            lambda closes, period=14: (
                59.0 if closes[-1] == 109.0 else 61.0
            )
        ),
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_atr",
        staticmethod(
            lambda candles, period=14: 2.0
        ),
    )
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_futures_vwap",
        staticmethod(lambda candles: 113.0),
    )
    assert (
        evaluate_strategy_d_signal(
            history,
            _futures(day, 112.0),
            _levels(day),
        )
        is None
    )


def test_lifecycle_hard_stop_precedes_scale_out(monkeypatch):
    signal = _signal()
    base = signal.timestamp
    history = [
        _bar(
            base - timedelta(minutes=5),
            close=111.0,
        )
    ]
    future = [
        _bar(
            base,
            close=112.0,
            high=116.0,
            low=107.5,
        ),
    ]
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_ema",
        staticmethod(
            lambda closes, period=9: 110.0
        ),
    )
    result = (
        StrategyDPositionManager()
        .replay_underlying_lifecycle(
            signal,
            history_through_entry=history,
            future_bars=future,
        )
    )
    assert result.runner_exit_reason == "ATR_HARD_STOP"
    assert result.realized_r == -1.0
    assert result.scale_out_time is None


def test_scale_half_then_ema_runner_exit(monkeypatch):
    signal = _signal()
    base = signal.timestamp
    history = [
        _bar(
            base - timedelta(minutes=5),
            close=111.0,
        )
    ]
    future = [
        _bar(
            base,
            close=115.0,
            high=116.0,
            low=111.2,
        ),
        _bar(
            base + timedelta(minutes=5),
            close=113.0,
            high=114.0,
            low=111.2,
        ),
    ]
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_ema",
        staticmethod(
            lambda closes, period=9: 113.5
        ),
    )
    result = (
        StrategyDPositionManager()
        .replay_underlying_lifecycle(
            signal,
            history_through_entry=history,
            future_bars=future,
        )
    )
    assert result.scale_out_price == 115.5
    assert result.scale_out_fraction == 0.5
    assert result.final_stop == 111.0
    assert result.runner_exit_reason == "EMA9_CLOSE_CROSS"
    assert result.runner_exit_price == 113.0
    assert result.realized_r > 1.0


def test_runner_exits_at_next_pivot(monkeypatch):
    signal = _signal()
    base = signal.timestamp
    history = [
        _bar(
            base - timedelta(minutes=5),
            close=111.0,
        )
    ]
    future = [
        _bar(
            base,
            close=115.0,
            high=116.0,
            low=111.2,
        ),
        _bar(
            base + timedelta(minutes=5),
            close=119.0,
            high=120.5,
            low=112.0,
        ),
    ]
    monkeypatch.setattr(
        FeatureEngine,
        "calculate_ema",
        staticmethod(
            lambda closes, period=9: 100.0
        ),
    )
    result = (
        StrategyDPositionManager()
        .replay_underlying_lifecycle(
            signal,
            history_through_entry=history,
            future_bars=future,
        )
    )
    assert result.runner_exit_reason == "NEXT_PIVOT_R2"
    assert result.runner_exit_price == 120.0
    assert result.scale_out_price == 115.5


def test_scale_out_plan_preserves_whole_lots():
    manager = StrategyDPositionManager()
    assert manager.scale_out_lots(1) == 0
    assert manager.scale_out_lots(2) == 1
    assert manager.scale_out_lots(3) == 1
    assert manager.scale_out_lots(4) == 2


def test_option_sizing_uses_contract_lot_size():
    manager = StrategyDPositionManager()
    lots, quantity = manager.size_option_position(
        entry_premium=50.0,
        lot_size=65,
        account_equity=500000.0,
    )
    assert lots >= 1
    assert quantity == lots * 65
