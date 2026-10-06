"""Build a signal-time-only blind AI context packet from a frozen backtest report.

The output deliberately excludes outcomes, exits, P&L, MFE/MAE and post-signal
candles. It is intended for blind AI context assessment before outcome reveal.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 15)
CLOSE = time(15, 30)


@dataclass(frozen=True, slots=True)
class Bar:
    instrument_id: str
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int


@dataclass(frozen=True, slots=True)
class Config:
    fast: int = 3
    slow: int = 10
    signal: int = 16
    rsi: int = 14


def _sma(values: Sequence[float | None], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1:i + 1]
        if all(v is not None for v in window):
            out[i] = sum(float(v) for v in window) / length
    return out


def _rsi_wilder(closes: Sequence[float], length: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= length:
        return out
    gains, losses = [], []
    for i in range(1, length + 1):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    gain, loss = sum(gains) / length, sum(losses) / length

    def value(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[length] = value(gain, loss)
    for i in range(length + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = (gain * (length - 1) + max(change, 0.0)) / length
        loss = (loss * (length - 1) + max(-change, 0.0)) / length
        out[i] = value(gain, loss)
    return out


def aggregate_3m(bars: Sequence[Bar]) -> list[Bar]:
    buckets: dict[tuple[object, int], dict[datetime, Bar]] = {}
    for bar in bars:
        local = bar.start.astimezone(IST)
        start = datetime.combine(local.date(), OPEN, tzinfo=IST)
        end = datetime.combine(local.date(), CLOSE, tzinfo=IST)
        if not start <= local < end:
            continue
        offset = int((local - start).total_seconds() // 60)
        buckets.setdefault((local.date(), offset // 3), {})[local] = bar
    out: list[Bar] = []
    for (day, bucket), rows in sorted(buckets.items()):
        start = datetime.combine(day, OPEN, tzinfo=IST) + timedelta(minutes=bucket * 3)
        expected = [start + timedelta(minutes=i) for i in range(3)]
        if any(ts not in rows for ts in expected):
            continue
        group = [rows[ts] for ts in expected]
        out.append(
            Bar(
                group[0].instrument_id,
                start.astimezone(timezone.utc),
                (start + timedelta(minutes=3)).astimezone(timezone.utc),
                group[0].open,
                max(x.high for x in group),
                min(x.low for x in group),
                group[-1].close,
                sum(x.volume for x in group),
                group[-1].oi,
            )
        )
    return out


def indicator_series(bars: Sequence[Bar], cfg: Config):
    closes = [b.close for b in bars]
    fast, slow = _sma(closes, cfg.fast), _sma(closes, cfg.slow)
    macd = [
        None if fast[i] is None or slow[i] is None else fast[i] - slow[i]
        for i in range(len(bars))
    ]
    signal = _sma(macd, cfg.signal)
    rsi = _rsi_wilder(closes, cfg.rsi)
    return macd, signal, rsi


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _canonical_nifty_5m(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT source,start_time,end_time,open,high,low,close
               FROM historical_candles
               WHERE instrument_id='INST-NIFTY-INDEX' AND interval='5m'
                 AND source IN ('BREEZE','KITE')
               ORDER BY start_time"""
        ).fetchall()
    finally:
        conn.close()

    by_start: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        start = _dt(row["start_time"])
        local = start.astimezone(IST)
        if not OPEN <= local.time() < CLOSE:
            continue
        item = {
            "source": str(row["source"]),
            "start": start,
            "end": _dt(row["end_time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        previous = by_start.get(start)
        if previous is None or (
            previous["source"] != "KITE" and item["source"] == "KITE"
        ):
            by_start[start] = item
    return [by_start[key] for key in sorted(by_start)]


def _wilder_atr14(bars: Sequence[dict[str, Any]]) -> list[float | None]:
    trs: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            trs.append(bar["high"] - bar["low"])
        else:
            prev_close = bars[i - 1]["close"]
            trs.append(
                max(
                    bar["high"] - bar["low"],
                    abs(bar["high"] - prev_close),
                    abs(bar["low"] - prev_close),
                )
            )
    out: list[float | None] = [None] * len(trs)
    if len(trs) < 14:
        return out
    value = sum(trs[:14]) / 14
    out[13] = value
    for i in range(14, len(trs)):
        value = (value * 13 + trs[i]) / 14
        out[i] = value
    return out


def _latest_confirmed_swings(
    completed: Sequence[dict[str, Any]],
) -> tuple[tuple[float, datetime], tuple[float, datetime]]:
    high: tuple[float, datetime] | None = None
    low: tuple[float, datetime] | None = None
    n = len(completed)
    for i in range(max(2, n - 60), n - 2):
        bar = completed[i]
        if (
            bar["high"] > completed[i - 1]["high"]
            and bar["high"] > completed[i - 2]["high"]
            and bar["high"] >= completed[i + 1]["high"]
            and bar["high"] >= completed[i + 2]["high"]
        ):
            high = (bar["high"], bar["start"])
        if (
            bar["low"] < completed[i - 1]["low"]
            and bar["low"] < completed[i - 2]["low"]
            and bar["low"] <= completed[i + 1]["low"]
            and bar["low"] <= completed[i + 2]["low"]
        ):
            low = (bar["low"], bar["start"])

    recent = list(completed[-12:])
    if not recent:
        raise RuntimeError("No completed NIFTY 5-minute bars available at signal time")
    if high is None:
        bar = max(recent, key=lambda x: x["high"])
        high = (bar["high"], bar["start"])
    if low is None:
        bar = min(recent, key=lambda x: x["low"])
        low = (bar["low"], bar["start"])
    return high, low


def _load_option_3m(path: Path, instrument_id: str) -> list[Bar]:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT instrument_id,start_time,end_time,open,high,low,close,volume,open_interest
               FROM historical_candles
               WHERE instrument_id=? AND interval='1m' AND source='BREEZE'
               ORDER BY start_time""",
            (instrument_id,),
        ).fetchall()
    finally:
        conn.close()
    raw = [
        Bar(
            str(r["instrument_id"]),
            _dt(r["start_time"]),
            _dt(r["end_time"]),
            float(r["open"]),
            float(r["high"]),
            float(r["low"]),
            float(r["close"]),
            int(r["volume"] or 0),
            int(r["open_interest"] or 0),
        )
        for r in rows
    ]
    return aggregate_3m(raw)


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def build_packet(historical_db: Path, baseline_json: Path) -> dict[str, Any]:
    report = json.loads(baseline_json.read_text(encoding="utf-8"))
    trades = report.get("trades") or []
    if not trades:
        raise RuntimeError("Baseline report contains no trades")

    nifty = _canonical_nifty_5m(historical_db)
    atr = _wilder_atr14(nifty)
    nifty_index_by_end = {bar["end"]: i for i, bar in enumerate(nifty)}
    option_cache: dict[
        str,
        tuple[
            list[Bar],
            tuple[list[float | None], list[float | None], list[float | None]],
            dict[datetime, int],
        ],
    ] = {}
    cfg = Config()
    output: list[dict[str, Any]] = []

    for trade_id, trade in enumerate(trades, 1):
        signal = _dt(trade["signal_time_ist"])
        day = signal.astimezone(IST).date()
        completed = [bar for bar in nifty if bar["end"] <= signal]
        last12 = completed[-12:]
        if not last12:
            raise RuntimeError(f"No completed NIFTY 5m context for trade {trade_id}")

        nidx = nifty_index_by_end[last12[-1]["end"]]
        atr_value = atr[nidx]
        if atr_value is None:
            raise RuntimeError(f"ATR unavailable for trade {trade_id}")

        day_bars = [
            bar
            for bar in nifty
            if bar["start"].astimezone(IST).date() == day and bar["end"] <= signal
        ]
        session_rows = [
            bar for bar in nifty if bar["start"].astimezone(IST).date() == day
        ]
        if not session_rows:
            raise RuntimeError(f"No NIFTY 5m session rows for trade {trade_id}")
        day_open = session_rows[0]["open"]
        if day_bars:
            day_high = max(x["high"] for x in day_bars)
            day_low = min(x["low"] for x in day_bars)
        else:
            day_high = day_low = day_open

        swing_high, swing_low = _latest_confirmed_swings(completed)

        instrument_id = str(trade["instrument_id"])
        if instrument_id not in option_cache:
            bars = _load_option_3m(historical_db, instrument_id)
            indicators = indicator_series(bars, cfg)
            by_end = {bar.end: i for i, bar in enumerate(bars)}
            option_cache[instrument_id] = (bars, indicators, by_end)
        bars, (macd, signal_line, rsi), by_end = option_cache[instrument_id]
        oi = by_end.get(signal)
        if oi is None:
            raise RuntimeError(
                f"Option signal bar missing for trade {trade_id}: "
                f"{instrument_id} {signal.isoformat()}"
            )

        current_macd = macd[oi]
        current_signal = signal_line[oi]
        current_rsi = rsi[oi]
        if any(x is None for x in (current_macd, current_signal, current_rsi)):
            raise RuntimeError(f"Option indicators unavailable for trade {trade_id}")

        checks = [
            (float(trade["entry_macd"]), float(current_macd), "MACD"),
            (float(trade["entry_signal"]), float(current_signal), "signal"),
            (float(trade["entry_rsi"]), float(current_rsi), "RSI"),
        ]
        for expected, actual, label in checks:
            if abs(expected - actual) > 1e-3:
                raise RuntimeError(
                    f"Trade {trade_id} {label} mismatch: "
                    f"baseline={expected} reconstructed={actual}"
                )

        hist = [
            None if macd[i] is None or signal_line[i] is None else macd[i] - signal_line[i]
            for i in range(len(bars))
        ]
        previous_hist = [hist[i] if i >= 0 else None for i in range(oi - 3, oi)]

        output.append(
            {
                "trade_id": trade_id,
                "date": str(trade["date"]),
                "signal_time_ist": signal.astimezone(IST).isoformat(),
                "direction": "PE",
                "nifty_5m_candles_last_12_completed": [
                    {
                        "start_ist": x["start"].astimezone(IST).isoformat(),
                        "end_ist": x["end"].astimezone(IST).isoformat(),
                        "open": x["open"],
                        "high": x["high"],
                        "low": x["low"],
                        "close": x["close"],
                    }
                    for x in last12
                ],
                "option_macd_3m": _round(current_macd),
                "option_macd_signal_3m": _round(current_signal),
                "option_macd_histogram_current_3m": _round(
                    float(current_macd) - float(current_signal)
                ),
                "option_macd_histogram_previous_3_3m": [
                    _round(x) for x in previous_hist
                ],
                "option_rsi14_3m": _round(current_rsi, 4),
                "nifty_atr14_5m": _round(atr_value, 4),
                "current_time_ist": signal.astimezone(IST).strftime("%H:%M"),
                "day_open": day_open,
                "day_high_so_far_from_completed_5m": day_high,
                "day_low_so_far_from_completed_5m": day_low,
                "recent_confirmed_swing_high": swing_high[0],
                "recent_confirmed_swing_high_time_ist": swing_high[1]
                .astimezone(IST)
                .isoformat(),
                "recent_confirmed_swing_low": swing_low[0],
                "recent_confirmed_swing_low_time_ist": swing_low[1]
                .astimezone(IST)
                .isoformat(),
                "proposed_trade_direction": "PE",
            }
        )

    return {
        "dataset_name": "MACD_RSI_AI_CONTEXT_PHASE1_BLIND",
        "trade_count": len(output),
        "blindness_note": (
            "Only signal-time or earlier information is included. No outcome, exit, "
            "MFE/MAE, post-signal candles, or P&L fields are present."
        ),
        "methodology": {
            "nifty_context": (
                "NIFTY index 5-minute price-action context. Timestamps are normalized "
                "and duplicate actual instants are removed; KITE is preferred when the "
                "same instant exists in KITE and BREEZE."
            ),
            "5m_window": (
                "Last 12 completed NIFTY 5-minute candles with candle end <= the option "
                "3-minute signal timestamp. Partially formed 5-minute candles are never included."
            ),
            "option_indicators": (
                "MACD(3,10) SMA, signal SMA(16), histogram and RSI(14) reconstructed "
                "from native BREEZE 1-minute option candles aggregated to 3-minute bars "
                "aligned from 09:15 IST."
            ),
            "atr": "Wilder ATR(14) on completed canonical NIFTY 5-minute bars.",
            "day_high_low": (
                "Only completed 5-minute bars are used. Before the first 5-minute close, "
                "day high/low are set to the known day open, rather than leaking the future "
                "high/low of an incomplete bar."
            ),
            "swing_levels": (
                "Latest confirmed 2-left/2-right pivot high and low visible by signal time. "
                "If none is available in the recent lookback, the extreme of the last 12 "
                "completed bars is used."
            ),
            "missing_histogram_history": (
                "Null values are retained when a frozen trade occurred before three prior "
                "MACD histogram values existed for that option series. No additional "
                "historical option data is introduced because that could change the frozen "
                "baseline indicators and trade list."
            ),
        },
        "trades": output,
    }


def write_csv(packet: dict[str, Any], path: Path) -> None:
    rows = []
    for trade in packet["trades"]:
        row = dict(trade)
        row["nifty_5m_candles_last_12_completed"] = json.dumps(
            row["nifty_5m_candles_last_12_completed"], separators=(",", ":")
        )
        row["option_macd_histogram_previous_3_3m"] = json.dumps(
            row["option_macd_histogram_previous_3_3m"], separators=(",", ":")
        )
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build outcome-free signal-time AI context packet"
    )
    parser.add_argument(
        "--historical-db",
        type=Path,
        default=Path("data/market/historical.db"),
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path)
    args = parser.parse_args()

    packet = build_packet(args.historical_db, args.baseline_json)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(packet, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.output_csv:
        write_csv(packet, args.output_csv)
    print(
        json.dumps(
            {
                "trade_count": packet["trade_count"],
                "output_json": str(args.output_json),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
