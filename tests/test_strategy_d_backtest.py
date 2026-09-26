from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from libs.contracts.models import Candle
from services.historical.strategy_d_sr_momentum_backtest import (
    _select_requested_session_dates,
    build_v2_comparison,
    run_backtest,
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
