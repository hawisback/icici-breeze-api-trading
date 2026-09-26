from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from libs.contracts.models import Candle
import services.historical.strategy_d_sr_momentum_backtest as d_backtest
from services.historical.strategy_d_sr_momentum_backtest import (
    _diagnose_strategy_d_bar,
    _has_complete_session_5m,
    _select_requested_session_dates,
    build_v2_comparison,
    run_backtest,
)
from services.strategy.strategies.sr_momentum_breakout import (
    StrategyDConfig,
)


IST = ZoneInfo("Asia/Kolkata")


def _bar(
    day: date,
    hour: int,
    minute: int,
    close: float,
    *,
    instrument_id: str,
    volume: int = 100,
) -> Candle:
    start = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=IST,
    )
    return Candle(
        instrument_id=instrument_id,
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=volume,
        open_interest=1000,
        source="BREEZE",
    )


def test_backtest_entry_point_is_read_only_and_strategy_isolated():
    previous = date(2026, 9, 22)
    current = date(2026, 9, 23)
    spot = [
        _bar(
            previous,
            9,
            15,
            100.0,
            instrument_id="INST-NIFTY-INDEX",
        ),
        _bar(
            previous,
            15,
            25,
            101.0,
            instrument_id="INST-NIFTY-INDEX",
        ),
    ]
    futures = []
    for index in range(20):
        minutes = 15 + index * 5
        hour = 9 + minutes // 60
        minute = minutes % 60
        spot.append(
            _bar(
                current,
                hour,
                minute,
                101.0 + index * 0.05,
                instrument_id="INST-NIFTY-INDEX",
            )
        )
        futures.append(
            _bar(
                current,
                hour,
                minute,
                102.0 + index * 0.05,
                instrument_id="INST-NIFTY-FUT-2026-09-29",
                volume=100 + index,
            )
        )

    report = run_backtest(
        spot_candles=spot,
        futures_candles=futures,
        start_date=current,
        end_date=current,
    )

    assert (
        report["strategy_id"]
        == "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V1"
    )
    assert report["usable_sessions"] == 1
    assert report["market_data_written"] is False
    assert report["broker_called"] is False
    assert report["production_thresholds_changed"] is False
    assert report["limitations"][-1] == (
        "Strategy A, Strategy B and Strategy C are not modified "
        "or evaluated by this module."
    )


def test_v2_comparison_preserves_corrected_v1_control():
    previous = date(2026, 9, 22)
    current = date(2026, 9, 23)
    spot = [
        _bar(
            previous,
            9,
            15,
            100.0,
            instrument_id="INST-NIFTY-INDEX",
        ),
        _bar(
            previous,
            15,
            25,
            101.0,
            instrument_id="INST-NIFTY-INDEX",
        ),
    ]
    futures = []
    for index in range(20):
        minutes = 15 + index * 5
        hour = 9 + minutes // 60
        minute = minutes % 60
        spot.append(
            _bar(
                current,
                hour,
                minute,
                101.0 + index * 0.05,
                instrument_id="INST-NIFTY-INDEX",
            )
        )
        futures.append(
            _bar(
                current,
                hour,
                minute,
                102.0 + index * 0.05,
                instrument_id="INST-NIFTY-FUT-2026-09-29",
                volume=100 + index,
            )
        )

    report = build_v2_comparison(
        spot_candles=spot,
        futures_candles=futures,
        start_date=current,
        end_date=current,
    )

    assert report["strategy_id"].endswith("V2_CANDIDATE")
    assert report["ruleset_version"] == 2
    assert report["config"]["minimum_rsi_clearance_points"] == 2.0
    assert report["config"]["max_previous_day_range_atr"] == 8.0
    comparison = report["comparison_to_corrected_v1"]
    assert comparison["control_strategy_id"].endswith("V1")
    assert comparison["control_config"]["variant"] == "V1_CONTROL"
    assert report["production_thresholds_changed"] is False



def test_exact_session_selector_uses_latest_dates_by_default():
    available = [
        date(2026, 9, 21),
        date(2026, 9, 22),
        date(2026, 9, 23),
        date(2026, 9, 24),
        date(2026, 9, 25),
    ]

    assert _select_requested_session_dates(
        available,
        sessions=3,
    ) == available[-3:]
    assert _select_requested_session_dates(
        available,
        sessions=2,
        start_date=date(2026, 9, 22),
    ) == [
        date(2026, 9, 22),
        date(2026, 9, 23),
    ]
    assert _select_requested_session_dates(
        available,
        sessions=2,
        end_date=date(2026, 9, 23),
    ) == [
        date(2026, 9, 22),
        date(2026, 9, 23),
    ]


def test_exact_session_selector_rejects_invalid_or_insufficient_request():
    available = [
        date(2026, 9, 22),
        date(2026, 9, 23),
    ]

    with pytest.raises(ValueError, match="greater than zero"):
        _select_requested_session_dates(
            available,
            sessions=0,
        )
    with pytest.raises(ValueError, match="only 2 candidate sessions"):
        _select_requested_session_dates(
            available,
            sessions=3,
        )


def test_backtest_filters_to_requested_sessions_and_reports_each_day():
    warmup = date(2026, 9, 22)
    sessions = [
        date(2026, 9, 23),
        date(2026, 9, 24),
        date(2026, 9, 25),
    ]
    spot = [
        _bar(
            warmup,
            9,
            15,
            100.0,
            instrument_id="INST-NIFTY-INDEX",
        ),
        _bar(
            warmup,
            15,
            25,
            100.5,
            instrument_id="INST-NIFTY-INDEX",
        ),
    ]
    futures = []
    for day_offset, current in enumerate(sessions):
        for index in range(20):
            minutes = 15 + index * 5
            hour = 9 + minutes // 60
            minute = minutes % 60
            spot.append(
                _bar(
                    current,
                    hour,
                    minute,
                    101.0 + day_offset + index * 0.02,
                    instrument_id="INST-NIFTY-INDEX",
                )
            )
            futures.append(
                _bar(
                    current,
                    hour,
                    minute,
                    102.0 + day_offset + index * 0.02,
                    instrument_id="INST-NIFTY-FUT-2026-09-29",
                    volume=100 + index,
                )
            )

    requested = sessions[1:]
    report = run_backtest(
        spot_candles=spot,
        futures_candles=futures,
        session_dates=requested,
    )

    assert report["usable_sessions"] == 2
    assert report["session_summary"]["session_count"] == 2
    assert report["session_summary"]["session_dates"] == [
        day.isoformat() for day in requested
    ]
    assert (
        report["session_summary"]["trade_days"]
        + report["session_summary"]["no_trade_days"]
        == 2
    )
    assert set(report["session_summary"]["trades_by_date"]) == {
        day.isoformat() for day in requested
    }



def test_signal_diagnostic_identifies_rsi_crossed_before_breakout(monkeypatch):
    current = date(2026, 9, 23)
    history = []
    for index in range(16):
        close = 99.0
        if index == 15:
            close = 101.0
        history.append(
            _bar(
                current,
                9 + (15 + index * 5) // 60,
                (15 + index * 5) % 60,
                close,
                instrument_id="INST-NIFTY-INDEX",
            )
        )

    levels = SimpleNamespace(
        session_date=current,
        pdh=100.0,
        pdl=90.0,
        r1=120.0,
        s1=80.0,
    )
    monkeypatch.setattr(
        d_backtest.FeatureEngine,
        "calculate_rsi",
        lambda closes, period: 65.0 if len(closes) == 16 else 64.0,
    )
    monkeypatch.setattr(
        d_backtest.FeatureEngine,
        "calculate_atr",
        lambda candles, period: 2.0,
    )
    monkeypatch.setattr(
        d_backtest,
        "_futures_vwap_confirmation",
        lambda candles, through: (110.0, 100.0),
    )

    diagnostic = _diagnose_strategy_d_bar(
        spot_history=history,
        futures_history=[],
        levels=levels,
        config=StrategyDConfig.v1_control(),
        signal=None,
    )

    assert diagnostic["breakout_direction"] == "CALL"
    assert diagnostic["conditions"]["structural_breakout"] is True
    assert diagnostic["conditions"]["breakout_vwap_aligned"] is True
    assert (
        diagnostic["conditions"]["breakout_rsi_current_qualified"]
        is True
    )
    assert (
        diagnostic["conditions"]["breakout_rsi_already_qualified"]
        is True
    )
    assert (
        diagnostic["conditions"]["breakout_rsi_cross_same_bar"]
        is False
    )
    assert (
        diagnostic["primary_blocker"]
        == "RSI_ALREADY_QUALIFIED_BEFORE_BREAKOUT"
    )


def test_signal_diagnostic_recognizes_same_bar_v2_rsi_cross(monkeypatch):
    current = date(2026, 9, 23)
    history = []
    for index in range(16):
        close = 99.0
        if index == 15:
            close = 101.0
        history.append(
            _bar(
                current,
                9 + (15 + index * 5) // 60,
                (15 + index * 5) % 60,
                close,
                instrument_id="INST-NIFTY-INDEX",
            )
        )

    levels = SimpleNamespace(
        session_date=current,
        pdh=100.0,
        pdl=90.0,
        r1=120.0,
        s1=80.0,
    )
    monkeypatch.setattr(
        d_backtest.FeatureEngine,
        "calculate_rsi",
        lambda closes, period: 63.0 if len(closes) == 16 else 59.0,
    )
    monkeypatch.setattr(
        d_backtest.FeatureEngine,
        "calculate_atr",
        lambda candles, period: 2.0,
    )
    monkeypatch.setattr(
        d_backtest,
        "_futures_vwap_confirmation",
        lambda candles, through: (110.0, 100.0),
    )

    diagnostic = _diagnose_strategy_d_bar(
        spot_history=history,
        futures_history=[],
        levels=levels,
        config=StrategyDConfig.v2_candidate(),
        signal=object(),
    )

    assert diagnostic["conditions"]["breakout_rsi_cross_same_bar"] is True
    assert diagnostic["conditions"]["breakout_vwap_and_rsi_cross"] is True
    assert diagnostic["primary_blocker"] == "QUALIFIED_SIGNAL"


def test_v2_report_includes_candidate_and_control_signal_diagnostics():
    previous = date(2026, 9, 22)
    current = date(2026, 9, 23)
    spot = [
        _bar(
            previous,
            9,
            15,
            100.0,
            instrument_id="INST-NIFTY-INDEX",
        ),
        _bar(
            previous,
            15,
            25,
            101.0,
            instrument_id="INST-NIFTY-INDEX",
        ),
    ]
    futures = []
    for index in range(20):
        minutes = 15 + index * 5
        hour = 9 + minutes // 60
        minute = minutes % 60
        spot.append(
            _bar(
                current,
                hour,
                minute,
                101.0 + index * 0.05,
                instrument_id="INST-NIFTY-INDEX",
            )
        )
        futures.append(
            _bar(
                current,
                hour,
                minute,
                102.0 + index * 0.05,
                instrument_id="INST-NIFTY-FUT-2026-09-29",
                volume=100 + index,
            )
        )

    report = build_v2_comparison(
        spot_candles=spot,
        futures_candles=futures,
        start_date=current,
        end_date=current,
    )

    assert report["signal_diagnostics"]["bars_evaluated"] == 20
    control = report["comparison_to_corrected_v1"]
    assert "control_signal_diagnostics" in control
    assert (
        control["control_signal_diagnostics"]["bars_evaluated"]
        == 20
    )



def test_complete_session_coverage_requires_every_expected_5m_bar():
    current = date(2026, 9, 23)
    bars = []
    for index in range(75):
        minutes = 15 + index * 5
        hour = 9 + minutes // 60
        minute = minutes % 60
        bars.append(
            _bar(
                current,
                hour,
                minute,
                100.0 + index * 0.01,
                instrument_id="INST-NIFTY-INDEX",
            )
        )

    assert _has_complete_session_5m(bars, current) is True
    assert _has_complete_session_5m(bars[:-1], current) is False
    assert _has_complete_session_5m(bars[:4], current) is False
