from datetime import datetime, timedelta

from libs.contracts.models import Candle
from services.historical.strategy_a_data_audit import IST
from services.historical.strategy_cd_multitimeframe_discovery import (
    DiscoveryConfig,
    MTFFeature,
    Trade,
    Trigger,
    _find_trigger,
    _frequency_band,
    _metrics,
    _run_lifecycle,
)


def _bar(
    start: datetime,
    minutes: int,
    *,
    interval: str,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> Candle:
    return Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        interval=interval,
        start_time=start,
        end_time=start + timedelta(minutes=minutes),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
        open_interest=1000,
        source="BREEZE",
    )


def _feature(bar: Candle, *, atr: float = 1.0) -> MTFFeature:
    return MTFFeature(
        candle=bar,
        ema20=99.5,
        ema50=99.0,
        adx14=24.0,
        plus_di14=30.0,
        minus_di14=15.0,
        atr14=atr,
        session_vwap=99.5,
    )


def test_trigger_cannot_use_1m_bar_that_started_before_5m_setup_close():
    setup_start = datetime(2026, 9, 21, 9, 55, tzinfo=IST)
    previous = _bar(
        setup_start - timedelta(minutes=5),
        5,
        interval="5m",
        open_=99.0,
        high=99.5,
        low=98.8,
        close=99.2,
    )
    setup = _bar(
        setup_start,
        5,
        interval="5m",
        open_=99.4,
        high=100.0,
        low=99.0,
        close=99.8,
    )
    pre_close_break = _bar(
        setup.end_time - timedelta(minutes=1),
        1,
        interval="1m",
        open_=99.7,
        high=100.4,
        low=99.6,
        close=100.3,
    )
    valid_break = _bar(
        setup.end_time,
        1,
        interval="1m",
        open_=99.9,
        high=100.3,
        low=99.8,
        close=100.2,
    )

    trigger = _find_trigger(
        family="di_continuation",
        direction="CALL",
        current=_feature(setup),
        previous=_feature(previous),
        one_minute=[pre_close_break, valid_break],
        config=DiscoveryConfig(),
        context=_feature(setup),
    )

    assert trigger is not None
    assert trigger.entry_bar.start_time == setup.end_time
    assert trigger.entry_bar.end_time == setup.end_time + timedelta(minutes=1)
    assert trigger.research_features["trigger_delay_minutes"] == 1.0
    assert trigger.research_features["risk_atr"] is not None
    assert trigger.research_features["context_15m_adx14"] == 24.0


def test_micro_breakout_requires_retest_then_later_1m_break():
    setup_start = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    previous = _bar(
        setup_start - timedelta(minutes=5),
        5,
        interval="5m",
        open_=99.2,
        high=100.0,
        low=99.0,
        close=99.8,
    )
    setup = _bar(
        setup_start,
        5,
        interval="5m",
        open_=100.1,
        high=101.0,
        low=100.2,
        close=100.8,
    )
    retest = _bar(
        setup.end_time,
        1,
        interval="1m",
        open_=100.2,
        high=100.2,
        low=99.98,
        close=100.05,
    )
    break_after_retest = _bar(
        setup.end_time + timedelta(minutes=1),
        1,
        interval="1m",
        open_=100.1,
        high=100.45,
        low=100.08,
        close=100.35,
    )

    trigger = _find_trigger(
        family="micro_breakout_retest",
        direction="CALL",
        current=_feature(setup),
        previous=_feature(previous),
        one_minute=[retest, break_after_retest],
        config=DiscoveryConfig(),
    )

    assert trigger is not None
    assert trigger.entry_bar == break_after_retest
    assert trigger.entry_price == 100.35


def test_lifecycle_uses_stop_first_for_same_1m_stop_target_ambiguity():
    start = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    entry_bar = _bar(
        start,
        1,
        interval="1m",
        open_=99.8,
        high=100.1,
        low=99.7,
        close=100.0,
    )
    ambiguous = _bar(
        start + timedelta(minutes=1),
        1,
        interval="1m",
        open_=100.0,
        high=102.2,
        low=98.8,
        close=101.5,
    )
    trigger = Trigger(
        family="di_continuation",
        direction="CALL",
        setup_end=start,
        entry_bar=entry_bar,
        entry_price=100.0,
        initial_stop=99.0,
        setup_atr=1.0,
    )

    trade = _run_lifecycle(trigger, [entry_bar, ambiguous], DiscoveryConfig())

    assert trade.exit_reason == "STOP_OR_TRAIL"
    assert trade.exit_price == 99.0
    assert trade.realized_r == -1.0


def test_metrics_report_drawdown_losing_streak_profit_factor_and_frequency():
    base = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    values = [1.0, -1.0, -1.0, 2.0]
    trades = [
        Trade(
            family="di_continuation",
            direction="CALL" if index % 2 == 0 else "PUT",
            setup_end=base + timedelta(days=index),
            entry_time=base + timedelta(days=index),
            exit_time=base + timedelta(days=index, minutes=10),
            entry_price=100.0,
            initial_stop=99.0,
            exit_price=100.0 + value,
            realized_r=value,
            mfe_r=max(value, 0.5),
            mae_r=1.0 if value < 0 else 0.25,
            exit_reason="TEST",
        )
        for index, value in enumerate(values)
    ]

    result = _metrics(trades, usable_sessions=10)

    assert result["trades"] == 4
    assert result["win_rate_pct"] == 50.0
    assert result["total_r"] == 1.0
    assert result["max_drawdown_r"] == -2.0
    assert result["max_losing_streak"] == 2
    assert result["profit_factor"] == 1.5
    assert result["trades_per_10_sessions"] == 4.0
    assert result["frequency_band"] == "~1 trade / 3 sessions"


def test_frequency_band_does_not_call_one_trade_per_ten_sessions_one_per_five():
    assert _frequency_band(0.1077) == "<1 trade / 5 sessions"
    assert _frequency_band(0.20) == "~1 trade / 5 sessions"
    assert _frequency_band(0.30) == "~1 trade / 3 sessions"
    assert _frequency_band(0.50) == "~1 trade / 2 sessions"
    assert _frequency_band(1.0) == "~1 trade / session"
    assert _frequency_band(1.5) == "~1-2 trades / session"
