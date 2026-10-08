"""Objective market calculations exposed to AI consumers.

This module intentionally produces measurements only. It must not emit trade
recommendations, directional labels, setup scores, confidence scores, or ranked
contracts. Interpretation belongs to the AI consumer.
"""

from __future__ import annotations

from datetime import time
from typing import Any, Sequence

from libs.contracts.models import Candle
from libs.market_time import IST

REAL_MARKET_SOURCES = {"BREEZE", "KITE", "LIVE"}


def is_real_market_source(source: object) -> bool:
    return str(source or "").upper() in REAL_MARKET_SOURCES


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def _ema_series(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    current = seed
    for index in range(period, len(values)):
        current = (values[index] - current) * alpha + current
        out[index] = current
    return out


def _rsi_wilder(values: Sequence[float], period: int = 14) -> float | None:
    if period <= 0 or len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        avg_gain = ((avg_gain * (period - 1)) + max(change, 0.0)) / period
        avg_loss = ((avg_loss * (period - 1)) + max(-change, 0.0)) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr_wilder(candles: Sequence[Candle], period: int = 14) -> float | None:
    if period <= 0 or len(candles) < period:
        return None
    trs: list[float] = []
    for index, candle in enumerate(candles):
        if index == 0:
            tr = candle.high - candle.low
        else:
            previous_close = candles[index - 1].close
            tr = max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        trs.append(float(tr))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = ((atr * (period - 1)) + tr) / period
    return atr


def _vwap(candles: Sequence[Candle]) -> float | None:
    total_volume = sum(max(0, int(candle.volume or 0)) for candle in candles)
    if total_volume <= 0:
        return None
    numerator = 0.0
    for candle in candles:
        volume = max(0, int(candle.volume or 0))
        typical_price = (candle.high + candle.low + candle.close) / 3.0
        numerator += typical_price * volume
    return numerator / total_volume


def _session_vwap(candles: Sequence[Candle]) -> float | None:
    """Volume-weighted typical price for the last candle's IST trading session."""
    if not candles:
        return None
    session_date = candles[-1].end_time.astimezone(IST).date()
    regular = [
        candle for candle in candles
        if candle.start_time.astimezone(IST).date() == session_date
        and time(9, 15) <= candle.start_time.astimezone(IST).time() < time(15, 30)
    ]
    return _vwap(regular)


def _pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100.0


def compute_technicals(candles: Sequence[Candle]) -> dict[str, Any]:
    """Calculate objective technical measurements from completed candles."""
    ordered = sorted(candles, key=lambda candle: candle.end_time)
    if not ordered:
        return {
            "data_points": 0,
            "latest_close": None,
            "ema_9": None,
            "ema_20": None,
            "ema_50": None,
            "rsi_14": None,
            "macd": None,
            "macd_signal": None,
            "macd_histogram": None,
            "macd_histogram_prev": None,
            "macd_histogram_prev2": None,
            "macd_crossed_above_zero": False,
            "macd_crossed_below_zero": False,
            "macd_histogram_expanding_positive": False,
            "macd_histogram_expanding_negative": False,
            "rsi_14_prev": None,
            "rsi_14_prev2": None,
            "rsi_crossed_above_55": False,
            "rsi_crossed_below_45": False,
            "bar_end_time": None,
            "atr_14": None,
            "vwap": None,
            "session_vwap": None,
            "vwap_scope": "requested_candles",
            "return_1_bar_pct": None,
            "return_3_bar_pct": None,
        }

    closes = [float(candle.close) for candle in ordered]
    ema_9 = _ema_series(closes, 9)
    ema_20 = _ema_series(closes, 20)
    ema_50 = _ema_series(closes, 50)
    ema_fast = _ema_series(closes, 12)
    ema_slow = _ema_series(closes, 26)

    macd_values: list[float] = []
    for fast, slow in zip(ema_fast, ema_slow, strict=True):
        if fast is not None and slow is not None:
            macd_values.append(fast - slow)
    macd_signal_series = _ema_series(macd_values, 9) if macd_values else []
    macd_value = macd_values[-1] if macd_values else None
    macd_signal = macd_signal_series[-1] if macd_signal_series else None
    # Match MACD values to signal EMA positions; only completed bars count.
    # Null is intentional for insufficient warmup, never a fabricated zero.
    valid_histogram = [
        macd - signal
        for macd, signal in zip(
            macd_values, macd_signal_series, strict=True
        )
        if signal is not None
    ]
    macd_histogram = valid_histogram[-1] if valid_histogram else None
    hist_prev = valid_histogram[-2] if len(valid_histogram) >= 2 else None
    hist_prev2 = valid_histogram[-3] if len(valid_histogram) >= 3 else None
    rsi_now = _rsi_wilder(closes, 14)
    rsi_prev = _rsi_wilder(closes[:-1], 14)
    rsi_prev2 = _rsi_wilder(closes[:-2], 14)
    macd_cross_up = (
        hist_prev is not None and macd_histogram is not None
        and hist_prev <= 0 < macd_histogram
    )
    macd_cross_down = (
        hist_prev is not None and macd_histogram is not None
        and hist_prev >= 0 > macd_histogram
    )

    return {
        "data_points": len(ordered),
        "latest_close": _round(closes[-1]),
        "ema_9": _round(ema_9[-1]),
        "ema_20": _round(ema_20[-1]),
        "ema_50": _round(ema_50[-1]),
        "rsi_14": _round(rsi_now),
        "rsi_14_prev": _round(rsi_prev),
        "rsi_14_prev2": _round(rsi_prev2),
        "rsi_crossed_above_55": bool(rsi_now is not None and rsi_prev is not None and rsi_prev <= 55 < rsi_now),
        "rsi_crossed_below_45": bool(rsi_now is not None and rsi_prev is not None and rsi_prev >= 45 > rsi_now),
        "macd": _round(macd_value),
        "macd_signal": _round(macd_signal),
        "macd_histogram": _round(macd_histogram),
        "macd_histogram_prev": _round(hist_prev),
        "macd_histogram_prev2": _round(hist_prev2),
        "macd_crossed_above_zero": macd_cross_up,
        "macd_crossed_below_zero": macd_cross_down,
        "macd_histogram_expanding_positive": bool(
            macd_histogram is not None and hist_prev is not None
            and macd_histogram > 0 and macd_histogram > hist_prev
        ),
        "macd_histogram_expanding_negative": bool(
            macd_histogram is not None and hist_prev is not None
            and macd_histogram < 0 and macd_histogram < hist_prev
        ),
        "bar_end_time": ordered[-1].end_time.isoformat(),
        "atr_14": _round(_atr_wilder(ordered, 14)),
        "vwap": _round(_vwap(ordered)),
        "session_vwap": _round(_session_vwap(ordered)),
        "vwap_scope": "requested_candles",
        "return_1_bar_pct": _round(
            _pct_change(closes[-1], closes[-2]) if len(closes) >= 2 else None
        ),
        "return_3_bar_pct": _round(
            _pct_change(closes[-1], closes[-4]) if len(closes) >= 4 else None
        ),
    }


def summarize_option_chain(chain: dict[str, Any]) -> dict[str, Any]:
    """Aggregate option-chain measurements without interpreting their direction."""
    call_oi = 0
    put_oi = 0
    call_volume = 0
    put_volume = 0
    call_oi_change = 0
    put_oi_change = 0
    call_contracts = 0
    put_contracts = 0
    call_oi_change_complete = True
    put_oi_change_complete = True

    for row in chain.get("strikes", []) or []:
        call = row.get("call") or {}
        put = row.get("put") or {}
        if call:
            call_contracts += 1
            call_oi += int(call.get("open_interest") or 0)
            call_volume += int(call.get("volume") or 0)
            if call.get("oi_change") is None:
                call_oi_change_complete = False
            else:
                call_oi_change += int(call["oi_change"])
        if put:
            put_contracts += 1
            put_oi += int(put.get("open_interest") or 0)
            put_volume += int(put.get("volume") or 0)
            if put.get("oi_change") is None:
                put_oi_change_complete = False
            else:
                put_oi_change += int(put["oi_change"])

    return {
        "source": chain.get("source"),
        "spot_price": chain.get("spot_price"),
        "expiry": chain.get("expiry"),
        "atm_strike": chain.get("atm_strike"),
        "strike_count": len(chain.get("strikes", []) or []),
        "call_contract_count": call_contracts,
        "put_contract_count": put_contracts,
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "call_oi_change": call_oi_change if call_oi_change_complete else None,
        "put_oi_change": put_oi_change if put_oi_change_complete else None,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "pcr_oi": _round(put_oi / call_oi if call_oi else None),
        "pcr_volume": _round(put_volume / call_volume if call_volume else None),
    }
