from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from libs.contracts.models import Candle
from services.historical.strategy_d_sr_momentum_backtest import (
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
