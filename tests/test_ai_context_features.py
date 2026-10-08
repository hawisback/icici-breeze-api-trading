from datetime import datetime, timedelta, timezone

from libs.contracts.models import Candle
from services.ai_context.features import compute_technicals, summarize_option_chain


def _candles(count: int = 60) -> list[Candle]:
    start = datetime(2026, 10, 7, 3, 45, tzinfo=timezone.utc)
    rows: list[Candle] = []
    price = 25000.0
    for index in range(count):
        close = price + index * 2.0
        rows.append(
            Candle(
                instrument_id="INST-NIFTY-INDEX",
                interval="5m",
                start_time=start + timedelta(minutes=index * 5),
                end_time=start + timedelta(minutes=(index + 1) * 5),
                open=close - 1.0,
                high=close + 3.0,
                low=close - 3.0,
                close=close,
                volume=1000 + index,
                open_interest=0,
                source="KITE",
            )
        )
    return rows


def test_compute_technicals_returns_measurements_only():
    metrics = compute_technicals(_candles())

    assert metrics["data_points"] == 60
    assert metrics["ema_9"] is not None
    assert metrics["ema_20"] is not None
    assert metrics["ema_50"] is not None
    assert metrics["rsi_14"] == 100.0
    assert metrics["macd"] is not None
    assert metrics["macd_signal"] is not None
    assert metrics["macd_histogram"] is not None
    assert metrics["atr_14"] is not None
    assert metrics["vwap"] is not None
    assert metrics["session_vwap"] is not None
    assert metrics["vwap_scope"] == "requested_candles"

    forbidden = {
        "bias",
        "direction",
        "recommendation",
        "confidence",
        "signal",
        "setup_score",
        "best_contract",
    }
    assert not forbidden.intersection(metrics)


def test_option_chain_summary_is_numeric_not_directional():
    chain = {
        "source": "KITE",
        "spot_price": 25242.0,
        "expiry": "2026-10-08",
        "atm_strike": 25250,
        "strikes": [
            {
                "strike": 25200,
                "call": {
                    "open_interest": 100,
                    "oi_change": 10,
                    "volume": 50,
                },
                "put": {
                    "open_interest": 200,
                    "oi_change": 20,
                    "volume": 100,
                },
            },
            {
                "strike": 25250,
                "call": {
                    "open_interest": 300,
                    "oi_change": -5,
                    "volume": 150,
                },
                "put": {
                    "open_interest": 200,
                    "oi_change": 5,
                    "volume": 100,
                },
            },
        ],
    }

    summary = summarize_option_chain(chain)

    assert summary["call_open_interest"] == 400
    assert summary["put_open_interest"] == 400
    assert summary["pcr_oi"] == 1.0
    assert summary["pcr_volume"] == 1.0
    assert "bias" not in summary
    assert "recommendation" not in summary


def test_session_vwap_resets_for_new_trading_day():
    candles = _candles(2)
    next_day = candles[1].model_copy(
        update={
            "start_time": candles[1].start_time + timedelta(days=1),
            "end_time": candles[1].end_time + timedelta(days=1),
            "high": 26010.0,
            "low": 25990.0,
            "close": 26000.0,
        }
    )
    metrics = compute_technicals([candles[0], next_day])
    assert metrics["session_vwap"] == 26000.0
    assert metrics["vwap"] != metrics["session_vwap"]


def test_kite_missing_oi_change_is_null_not_a_false_zero():
    chain = {
        "source": "KITE",
        "strikes": [
            {
                "strike": 25000,
                "call": {"open_interest": 5000, "volume": 300, "oi_change": None},
                "put": {"open_interest": 4000, "volume": 250, "oi_change": None},
            }
        ],
    }
    metrics = summarize_option_chain(chain)
    assert metrics["call_oi_change"] is None
    assert metrics["put_oi_change"] is None
    assert metrics["pcr_oi"] == 0.8
