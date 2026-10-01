"""Backtest Strategy F5 on session-aligned 2-minute option candles."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    CANDIDATES,
    COST_MODEL,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    RELATIVE_VOLATILITY_INDEX,
    REPORTING,
    STRATEGY_ID,
    STRATEGY_NAME,
    TRAIL,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_2MIN_MACD_RVI10_TRAIL_BACKTEST_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aggregate_2m(
    option_rows_1m: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate complete 1m pairs aligned from the 09:15 session open."""
    grouped: dict[
        tuple[str, int, str, str, int],
        list[dict[str, Any]],
    ] = defaultdict(list)
    incomplete: list[dict[str, Any]] = []
    session_start = time.fromisoformat(WINDOW["session_start"])

    for row in option_rows_1m:
        ts = datetime.fromisoformat(str(row["timestamp"]))
        base = datetime.combine(ts.date(), session_start, tzinfo=ts.tzinfo)
        offset = int((ts - base).total_seconds() // 60)
        if offset < 0:
            continue
        bucket = offset // 2
        key = (
            str(row["expiry"]),
            int(row["strike"]),
            str(row["right"]),
            ts.date().isoformat(),
            bucket,
        )
        grouped[key].append(row)

    bars: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda x: x["timestamp"])
        expiry, strike, right, day_text, bucket = key
        first_ts = datetime.fromisoformat(str(rows[0]["timestamp"]))
        base = datetime.combine(
            first_ts.date(),
            session_start,
            tzinfo=first_ts.tzinfo,
        )
        expected_start = base + timedelta(minutes=2 * bucket)
        expected = [
            expected_start,
            expected_start + timedelta(minutes=1),
        ]
        actual = [datetime.fromisoformat(str(r["timestamp"])) for r in rows]
        if len(rows) != 2 or actual != expected:
            incomplete.append({
                "expiry": expiry,
                "strike": strike,
                "right": right,
                "date": day_text,
                "bucket": bucket,
                "row_count": len(rows),
                "timestamps": [x.isoformat() for x in actual],
            })
            continue

        volumes = [r.get("volume") for r in rows if r.get("volume") is not None]
        bars.append({
            "timestamp": expected_start.isoformat(),
            "date": day_text,
            "expiry": expiry,
            "strike": strike,
            "right": right,
            "open": float(rows[0]["open"]),
            "high": max(float(r["high"]) for r in rows),
            "low": min(float(r["low"]) for r in rows),
            "close": float(rows[-1]["close"]),
            "volume": sum(float(v) for v in volumes) if volumes else None,
            "open_interest": rows[-1].get("open_interest"),
            "source": "BREEZE_1M_AGGREGATED_2M",
        })
    bars.sort(
        key=lambda r: (
            r["timestamp"],
            r["expiry"],
            r["strike"],
            r["right"],
        )
    )
    return bars, incomplete


def _indicator_frames(
    bars_2m: list[dict[str, Any]],
) -> dict[tuple[str, int, str], pd.DataFrame]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in bars_2m:
        grouped[
            (str(row["expiry"]), int(row["strike"]), str(row["right"]))
        ].append(row)

    std_len = int(RELATIVE_VOLATILITY_INDEX["stddev_length"])
    ema_len = int(RELATIVE_VOLATILITY_INDEX["directional_ema_length"])
    ddof = int(RELATIVE_VOLATILITY_INDEX["stddev_ddof"])
    result: dict[tuple[str, int, str], pd.DataFrame] = {}

    for key, rows in grouped.items():
        frame = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        close = frame["close"].astype(float)

        fast = close.ewm(span=int(MACD["fast_length"]), adjust=False).mean()
        slow = close.ewm(span=int(MACD["slow_length"]), adjust=False).mean()
        frame["macd"] = fast - slow
        frame["macd_signal"] = frame["macd"].ewm(
            span=int(MACD["signal_length"]),
            adjust=False,
        ).mean()
        frame["macd_prev"] = frame["macd"].shift(1)
        frame["macd_signal_prev"] = frame["macd_signal"].shift(1)

        stddev = close.rolling(std_len, min_periods=std_len).std(ddof=ddof)
        change = close.diff()
        upper_input = stddev.where(change > 0.0, 0.0)
        lower_input = stddev.where(change <= 0.0, 0.0)
        upper = upper_input.ewm(span=ema_len, adjust=False).mean()
        lower = lower_input.ewm(span=ema_len, adjust=False).mean()
        denom = upper + lower
        neutral = float(RELATIVE_VOLATILITY_INDEX["zero_denominator_value"])
        rvi = (100.0 * upper / denom).where(denom != 0.0, neutral)
        frame["relative_volatility_index"] = rvi
        result[key] = frame

    return result


def _observations(
    daily_contracts: list[dict[str, Any]],
    bars_2m: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, dict[str, Any]]],
    list[dict[str, Any]],
]:
    """Map each completed 2m bar to its next-open decision timestamp."""
    frames = _indicator_frames(bars_2m)
    min_prior = int(MACD["minimum_prior_2m_bars_before_session"])
    obs_by_day: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    insufficient: list[dict[str, Any]] = []

    for selected in daily_contracts:
        day = date.fromisoformat(str(selected["date"]))
        for right in OPTION_SELECTION["rights"]:
            key = (
                str(selected["expiry"]),
                int(selected["strike"]),
                str(right),
            )
            frame = frames.get(key)
            if frame is None or frame.empty:
                insufficient.append({
                    "date": day.isoformat(),
                    "right": right,
                    "reason": "NO_2M_OPTION_BARS",
                })
                continue

            indices = list(frame.index[frame["timestamp"].dt.date == day])
            if not indices:
                insufficient.append({
                    "date": day.isoformat(),
                    "right": right,
                    "reason": "NO_2M_SESSION_BARS",
                })
                continue

            if int(indices[0]) < min_prior:
                insufficient.append({
                    "date": day.isoformat(),
                    "right": right,
                    "reason": "INSUFFICIENT_2M_WARMUP",
                    "prior_bars": int(indices[0]),
                    "required_prior_bars": min_prior,
                })
                continue

            for idx in indices:
                if idx <= 0:
                    continue
                row = frame.loc[idx]
                if (
                    pd.isna(row["macd_prev"])
                    or pd.isna(row["macd_signal_prev"])
                    or pd.isna(row["relative_volatility_index"])
                ):
                    continue
                bar_start = pd.Timestamp(row["timestamp"]).to_pydatetime()
                decision_ts = bar_start + timedelta(minutes=2)
                bullish = (
                    float(row["macd_prev"]) <= float(row["macd_signal_prev"])
                    and float(row["macd"]) > float(row["macd_signal"])
                )
                bearish = (
                    float(row["macd_prev"]) >= float(row["macd_signal_prev"])
                    and float(row["macd"]) < float(row["macd_signal"])
                )
                obs_by_day[day.isoformat()].setdefault(
                    decision_ts.isoformat(),
                    {},
                )[right] = {
                    "date": day.isoformat(),
                    "month": str(selected["month"]),
                    "decision_timestamp": decision_ts.isoformat(),
                    "bar_start": bar_start.isoformat(),
                    "expiry": key[0],
                    "strike": key[1],
                    "right": right,
                    "close": float(row["close"]),
                    "macd": float(row["macd"]),
                    "macd_signal": float(row["macd_signal"]),
                    "rvi": float(row["relative_volatility_index"]),
                    "bullish_cross": bool(bullish),
                    "bearish_cross": bool(bearish),
                }

    return obs_by_day, insufficient


def _bar_open_lookup(
    bars_2m: list[dict[str, Any]],
) -> dict[tuple[str, str, int, str], float]:
    return {
        (
            str(row["timestamp"]),
            str(row["expiry"]),
            int(row["strike"]),
            str(row["right"]),
        ): float(row["open"])
        for row in bars_2m
    }


def _one_min_open_lookup(
    option_rows_1m: list[dict[str, Any]],
) -> dict[tuple[str, str, int, str], float]:
    return {
        (
            str(row["timestamp"]),
            str(row["expiry"]),
            int(row["strike"]),
            str(row["right"]),
        ): float(row["open"])
        for row in option_rows_1m
    }


def _eligible_entry(obs: dict[str, Any]) -> bool:
    return (
        bool(obs["bullish_cross"])
        and float(obs["rvi"])
        >= float(RELATIVE_VOLATILITY_INDEX["entry_threshold"])
    )


def _raw_trade_simulation(
    daily_contracts: list[dict[str, Any]],
    obs_by_day: dict[str, dict[str, dict[str, Any]]],
    open_2m: dict[tuple[str, str, int, str], float],
    open_1m: dict[tuple[str, str, int, str], float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    last_entry = time.fromisoformat(WINDOW["last_entry_time"])
    force_time = time.fromisoformat(WINDOW["force_exit_time"])
    trades: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []

    for selected in daily_contracts:
        day = str(selected["date"])
        observations = obs_by_day.get(day, {})
        current: dict[str, Any] | None = None

        for ts in sorted(observations):
            dt = datetime.fromisoformat(ts)
            if dt.time() > force_time:
                break
            same = observations[ts]

            if current is not None:
                held = same.get(str(current["right"]))
                if held is not None and bool(held["bearish_cross"]):
                    key = (
                        ts,
                        str(current["expiry"]),
                        int(current["strike"]),
                        str(current["right"]),
                    )
                    exit_open = open_2m.get(key)
                    if exit_open is None:
                        skips.append({
                            **current,
                            "exit_timestamp": ts,
                            "skip_reason": "MISSING_2M_EXIT_OPEN",
                        })
                    else:
                        trades.append({
                            **current,
                            "exit_timestamp": ts,
                            "exit_open": exit_open,
                            "exit_reason": "BEARISH_MACD_CROSS",
                            "trail_activated": False,
                        })
                    current = None

            if current is not None or dt.time() > last_entry:
                continue

            eligible = [x for x in same.values() if _eligible_entry(x)]
            if len(eligible) != 1:
                continue
            entry = eligible[0]
            key = (
                ts,
                str(entry["expiry"]),
                int(entry["strike"]),
                str(entry["right"]),
            )
            entry_open = open_2m.get(key)
            if entry_open is None:
                skips.append({
                    "date": day,
                    "entry_timestamp": ts,
                    "right": entry["right"],
                    "skip_reason": "MISSING_2M_ENTRY_OPEN",
                })
                continue
            current = {
                "candidate": "RAW_MACD_EXIT",
                "date": day,
                "month": str(selected["month"]),
                "entry_timestamp": ts,
                "right": str(entry["right"]),
                "expiry": str(entry["expiry"]),
                "strike": int(entry["strike"]),
                "entry_open": entry_open,
                "entry_rvi": float(entry["rvi"]),
            }

        if current is not None:
            entry_dt = datetime.fromisoformat(str(current["entry_timestamp"]))
            force_dt = datetime.combine(
                entry_dt.date(), force_time, tzinfo=entry_dt.tzinfo
            )
            key = (
                force_dt.isoformat(),
                str(current["expiry"]),
                int(current["strike"]),
                str(current["right"]),
            )
            exit_open = open_1m.get(key)
            if exit_open is None:
                skips.append({
                    **current,
                    "exit_timestamp": force_dt.isoformat(),
                    "skip_reason": "MISSING_1M_FORCE_EXIT_OPEN",
                })
            else:
                trades.append({
                    **current,
                    "exit_timestamp": force_dt.isoformat(),
                    "exit_open": exit_open,
                    "exit_reason": "FORCE_EXIT_15_20",
                    "trail_activated": False,
                })
    return trades, skips


def _trail_trade_simulation(
    daily_contracts: list[dict[str, Any]],
    obs_by_day: dict[str, dict[str, dict[str, Any]]],
    open_2m: dict[tuple[str, str, int, str], float],
    open_1m: dict[tuple[str, str, int, str], float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    last_entry = time.fromisoformat(WINDOW["last_entry_time"])
    force_time = time.fromisoformat(WINDOW["force_exit_time"])
    activate_at = float(TRAIL["activation_return_pct"])
    distance = float(TRAIL["distance_pct_of_initial_trade_capital"])
    trades: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []

    for selected in daily_contracts:
        day = str(selected["date"])
        observations = obs_by_day.get(day, {})
        current: dict[str, Any] | None = None

        for ts in sorted(observations):
            dt = datetime.fromisoformat(ts)
            if dt.time() > force_time:
                break
            same = observations[ts]

            # Manage the bar that just completed before considering new entries.
            if current is not None:
                held = same.get(str(current["right"]))
                if (
                    held is not None
                    and datetime.fromisoformat(str(current["entry_timestamp"])) < dt
                ):
                    close_ret = (
                        float(held["close"]) / float(current["entry_open"]) - 1.0
                    ) * 100.0

                    if bool(current["trail_activated"]):
                        active_floor = float(current["trail_floor_return_pct"])
                        if close_ret <= active_floor:
                            key = (
                                ts,
                                str(current["expiry"]),
                                int(current["strike"]),
                                str(current["right"]),
                            )
                            exit_open = open_2m.get(key)
                            if exit_open is None:
                                skips.append({
                                    **current,
                                    "exit_timestamp": ts,
                                    "skip_reason": "MISSING_2M_TRAIL_EXIT_OPEN",
                                })
                            else:
                                trades.append({
                                    **current,
                                    "exit_timestamp": ts,
                                    "exit_open": exit_open,
                                    "exit_reason": "CLOSE_CONFIRMED_TRAIL10",
                                    "exit_bar_close_return_pct": round(
                                        close_ret, 4
                                    ),
                                })
                            current = None
                        else:
                            peak = max(
                                float(current["peak_close_return_pct"]),
                                close_ret,
                            )
                            current["peak_close_return_pct"] = peak
                            current["trail_floor_return_pct"] = max(
                                active_floor,
                                max(0.0, peak - distance),
                            )

                    else:
                        # Activation has precedence over a same-bar bearish cross.
                        if close_ret >= activate_at:
                            current["trail_activated"] = True
                            current["trail_activation_timestamp"] = ts
                            current["peak_close_return_pct"] = close_ret
                            current["trail_floor_return_pct"] = max(
                                0.0, close_ret - distance
                            )
                        elif bool(held["bearish_cross"]):
                            key = (
                                ts,
                                str(current["expiry"]),
                                int(current["strike"]),
                                str(current["right"]),
                            )
                            exit_open = open_2m.get(key)
                            if exit_open is None:
                                skips.append({
                                    **current,
                                    "exit_timestamp": ts,
                                    "skip_reason": "MISSING_2M_MACD_EXIT_OPEN",
                                })
                            else:
                                trades.append({
                                    **current,
                                    "exit_timestamp": ts,
                                    "exit_open": exit_open,
                                    "exit_reason": "PRE_TRAIL_BEARISH_MACD_CROSS",
                                    "exit_bar_close_return_pct": round(
                                        close_ret, 4
                                    ),
                                })
                            current = None

            if current is not None or dt.time() > last_entry:
                continue

            eligible = [x for x in same.values() if _eligible_entry(x)]
            if len(eligible) != 1:
                continue
            entry = eligible[0]
            key = (
                ts,
                str(entry["expiry"]),
                int(entry["strike"]),
                str(entry["right"]),
            )
            entry_open = open_2m.get(key)
            if entry_open is None:
                skips.append({
                    "date": day,
                    "entry_timestamp": ts,
                    "right": entry["right"],
                    "skip_reason": "MISSING_2M_ENTRY_OPEN",
                })
                continue

            current = {
                "candidate": "TRAIL10_CLOSE_CONFIRMED",
                "date": day,
                "month": str(selected["month"]),
                "entry_timestamp": ts,
                "right": str(entry["right"]),
                "expiry": str(entry["expiry"]),
                "strike": int(entry["strike"]),
                "entry_open": entry_open,
                "entry_rvi": float(entry["rvi"]),
                "trail_activated": False,
                "trail_activation_timestamp": None,
                "peak_close_return_pct": 0.0,
                "trail_floor_return_pct": None,
            }

        if current is not None:
            entry_dt = datetime.fromisoformat(str(current["entry_timestamp"]))
            force_dt = datetime.combine(
                entry_dt.date(), force_time, tzinfo=entry_dt.tzinfo
            )
            key = (
                force_dt.isoformat(),
                str(current["expiry"]),
                int(current["strike"]),
                str(current["right"]),
            )
            exit_open = open_1m.get(key)
            if exit_open is None:
                skips.append({
                    **current,
                    "exit_timestamp": force_dt.isoformat(),
                    "skip_reason": "MISSING_1M_FORCE_EXIT_OPEN",
                })
            else:
                trades.append({
                    **current,
                    "exit_timestamp": force_dt.isoformat(),
                    "exit_open": exit_open,
                    "exit_reason": "FORCE_EXIT_15_20",
                    "exit_bar_close_return_pct": None,
                })

    return trades, skips


def _costs(entry_open: float, exit_open: float, slip: float) -> dict[str, float]:
    qty = int(OPTION_SELECTION["lot_size"])
    entry_fill = entry_open + slip
    exit_fill = max(0.0, exit_open - slip)
    buy = entry_fill * qty
    sell = exit_fill * qty
    turnover = buy + sell
    brokerage = 2.0 * float(COST_MODEL["brokerage_per_order_inr"])
    exchange = turnover * float(COST_MODEL["exchange_charge_rate"])
    stt = sell * float(COST_MODEL["stt_sell_rate"])
    sebi = turnover * float(COST_MODEL["sebi_charge_rate"])
    stamp = buy * float(COST_MODEL["stamp_buy_rate"])
    gst = (brokerage + exchange + sebi) * float(COST_MODEL["gst_rate"])
    explicit = brokerage + exchange + stt + sebi + stamp + gst
    gross = (exit_fill - entry_fill) * qty
    return {
        "entry_fill": round(entry_fill, 2),
        "exit_fill": round(exit_fill, 2),
        "gross_pnl_inr": round(gross, 2),
        "explicit_costs_inr": round(explicit, 2),
        "net_pnl_inr": round(gross - explicit, 2),
    }


def _decorate(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slips = [
        float(x)
        for x in COST_MODEL["slippage_sensitivity_points_each_side"]
    ]
    out: list[dict[str, Any]] = []
    for trade in trades:
        entry_dt = datetime.fromisoformat(str(trade["entry_timestamp"]))
        exit_dt = datetime.fromisoformat(str(trade["exit_timestamp"]))
        gross_return = (
            float(trade["exit_open"]) / float(trade["entry_open"]) - 1.0
        ) * 100.0
        peak = float(trade.get("peak_close_return_pct") or 0.0)
        out.append({
            **trade,
            "hold_minutes": int((exit_dt - entry_dt).total_seconds() // 60),
            "gross_exit_return_pct": round(gross_return, 4),
            "profit_giveback_from_peak_pct": (
                round(peak - gross_return, 4)
                if bool(trade.get("trail_activated"))
                else None
            ),
            "primary_cost_model": _costs(
                float(trade["entry_open"]),
                float(trade["exit_open"]),
                0.0,
            ),
            "slippage_sensitivity": {
                f"{slip:.2f}": _costs(
                    float(trade["entry_open"]),
                    float(trade["exit_open"]),
                    slip,
                )
                for slip in slips
            },
        })
    return out


def _pf(values: list[float]) -> float | None:
    pos = sum(v for v in values if v > 0)
    neg = -sum(v for v in values if v < 0)
    if neg == 0:
        return None if pos == 0 else float("inf")
    return pos / neg


def _max_dd(values: list[float]) -> float:
    equity = peak = dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dd


def _summary(
    trades: list[dict[str, Any]],
    slip_key: str = "0.00",
) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl_inr": 0.0,
            "profit_factor": None,
        }
    vals = [
        float(t["slippage_sensitivity"][slip_key]["net_pnl_inr"])
        for t in trades
    ]
    pf = _pf(vals)
    activated = [t for t in trades if bool(t.get("trail_activated"))]
    trail_exits = [
        t for t in trades if t.get("exit_reason") == "CLOSE_CONFIRMED_TRAIL10"
    ]
    givebacks = [
        float(t["profit_giveback_from_peak_pct"])
        for t in activated
        if t.get("profit_giveback_from_peak_pct") is not None
    ]
    return {
        "trades": len(trades),
        "wins": sum(v > 0 for v in vals),
        "losses": sum(v < 0 for v in vals),
        "win_rate_pct": round(
            sum(v > 0 for v in vals) / len(vals) * 100.0, 2
        ),
        "net_pnl_inr": round(sum(vals), 2),
        "average_net_pnl_inr": round(sum(vals) / len(vals), 2),
        "median_net_pnl_inr": round(float(pd.Series(vals).median()), 2),
        "profit_factor": (
            None
            if pf is None
            else ("INF" if math.isinf(pf) else round(float(pf), 4))
        ),
        "max_drawdown_inr": round(_max_dd(vals), 2),
        "average_hold_minutes": round(
            sum(int(t["hold_minutes"]) for t in trades) / len(trades), 2
        ),
        "trail_activation_count": len(activated),
        "trail_activation_rate_pct": round(
            len(activated) / len(trades) * 100.0, 2
        ),
        "trail_exit_count": len(trail_exits),
        "average_peak_close_return_pct": (
            round(
                sum(float(t.get("peak_close_return_pct") or 0.0) for t in activated)
                / len(activated),
                2,
            )
            if activated
            else None
        ),
        "average_final_trail_floor_return_pct": (
            round(
                sum(
                    float(t.get("trail_floor_return_pct") or 0.0)
                    for t in activated
                )
                / len(activated),
                2,
            )
            if activated
            else None
        ),
        "average_profit_giveback_from_peak_pct": (
            round(sum(givebacks) / len(givebacks), 2)
            if givebacks
            else None
        ),
    }


def _report(
    trades: list[dict[str, Any]],
    skips: list[dict[str, Any]],
) -> dict[str, Any]:
    by_month = {
        month: [t for t in trades if t["month"] == month]
        for month in WINDOW["months"]
    }
    return {
        "quality": {
            "scorable_trades": len(trades),
            "skipped_trade_intents": len(skips),
        },
        "pooled": {
            key: _summary(trades, key)
            for key in ("0.00", "0.50", "1.00")
        },
        "by_month": {
            month: {
                key: _summary(group, key)
                for key in ("0.00", "0.50", "1.00")
            }
            for month, group in by_month.items()
        },
        "ce_0_00": _summary(
            [t for t in trades if t["right"] == "CE"],
            "0.00",
        ),
        "pe_0_00": _summary(
            [t for t in trades if t["right"] == "PE"],
            "0.00",
        ),
        "exit_reason_counts": (
            {
                str(k): int(v)
                for k, v in pd.Series(
                    [str(t["exit_reason"]) for t in trades]
                ).value_counts().items()
            }
            if trades
            else {}
        ),
        "trades": trades,
        "skipped": skips,
    }


def backtest(
    payload: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("F5 market artifact protocol mismatch")
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market artifact must be outcome-unscored")

    daily_contracts = list(payload.get("daily_contracts") or [])
    option_rows_1m = list(payload.get("option_rows_1m") or [])
    bars_2m, incomplete = _aggregate_2m(option_rows_1m)
    obs_by_day, insufficient = _observations(daily_contracts, bars_2m)
    open_2m = _bar_open_lookup(bars_2m)
    open_1m = _one_min_open_lookup(option_rows_1m)

    raw_trades, raw_skips = _raw_trade_simulation(
        daily_contracts,
        obs_by_day,
        open_2m,
        open_1m,
    )
    trail_trades, trail_skips = _trail_trade_simulation(
        daily_contracts,
        obs_by_day,
        open_2m,
        open_1m,
    )
    raw_trades = _decorate(raw_trades)
    trail_trades = _decorate(trail_trades)

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "window": WINDOW,
        "rules": {
            "macd": MACD,
            "relative_volatility_index": RELATIVE_VOLATILITY_INDEX,
            "trail": TRAIL,
            "candidates": CANDIDATES,
            "cost_model": COST_MODEL,
        },
        "quality": {
            "selected_sessions": len(daily_contracts),
            "raw_1m_option_rows": len(option_rows_1m),
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
        },
        "candidates": {
            "RAW_MACD_EXIT": _report(raw_trades, raw_skips),
            "TRAIL10_CLOSE_CONFIRMED": _report(
                trail_trades,
                trail_skips,
            ),
        },
        "incomplete_2m_buckets_sample": incomplete[:100],
        "insufficient_warmup": insufficient,
        "reporting_contract": REPORTING,
        "guardrails": GUARDRAILS,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest Strategy F5 2m MACD RVI10 close-confirmed trail"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f5_2min_macd_rvi10_trail_2026_07_09.json"),
    )
    args = parser.parse_args()

    source_sha = _sha256(args.market)
    payload = json.loads(args.market.read_text(encoding="utf-8"))
    report = backtest(payload, source_sha256=source_sha)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    compact = {
        name: {
            "pooled": result["pooled"],
            "by_month": result["by_month"],
            "ce_0_00": result["ce_0_00"],
            "pe_0_00": result["pe_0_00"],
            "exit_reason_counts": result["exit_reason_counts"],
            "quality": result["quality"],
        }
        for name, result in report["candidates"].items()
    }
    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_market_sha256": source_sha,
        "quality": report["quality"],
        "candidate_summary": compact,
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
