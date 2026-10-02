"""Backtest Strategy F MACD Call/Put crossover on its sealed market artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from services.historical.strategy_f_macd_options_market import (
    _atm_strike,
    _macd_frame,
    _nearest_expiry,
)
from services.historical.strategy_f_macd_options_protocol import (
    BACKTEST_WINDOW,
    COST_MODEL,
    EXECUTION,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    REPORTING,
    SIGNAL,
    STRATEGY_ID,
    STRATEGY_NAME,
)

RESEARCH_TYPE = "STRATEGY_F_MACD_OPTIONS_BACKTEST_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _complete_backtest_days(spot_rows: list[dict[str, Any]]) -> tuple[set[str], list[dict[str, Any]]]:
    start = date.fromisoformat(BACKTEST_WINDOW["start"])
    end = date.fromisoformat(BACKTEST_WINDOW["end"])
    counts: dict[str, int] = defaultdict(int)
    for row in spot_rows:
        day = date.fromisoformat(str(row["date"]))
        if start <= day <= end:
            counts[day.isoformat()] += 1
    complete = {day for day, count in counts.items() if count == 75}
    partial = [
        {"date": day, "rows": count}
        for day, count in sorted(counts.items())
        if count != 75
    ]
    return complete, partial


def _all_crossover_events(
    spot_rows: list[dict[str, Any]],
    complete_days: set[str],
) -> list[dict[str, Any]]:
    frame = _macd_frame(spot_rows)
    start = date.fromisoformat(BACKTEST_WINDOW["start"])
    end = date.fromisoformat(BACKTEST_WINDOW["end"])
    force_exit = time.fromisoformat(BACKTEST_WINDOW["force_exit_time"])
    events: list[dict[str, Any]] = []

    for row in frame.itertuples(index=False):
        if pd.isna(row.macd_prev) or pd.isna(row.signal_prev):
            continue
        bullish = row.macd_prev <= row.signal_prev and row.macd > row.signal
        bearish = row.macd_prev >= row.signal_prev and row.macd < row.signal
        if not (bullish or bearish):
            continue

        bar_start = row.timestamp.to_pydatetime()
        day = bar_start.date()
        if not (start <= day <= end):
            continue
        if day.isoformat() not in complete_days:
            continue
        event_time = bar_start + timedelta(minutes=5)
        if event_time.time() > force_exit:
            continue

        bullish_side = bool(bullish)
        events.append(
            {
                "date": day.isoformat(),
                "signal_bar_start": bar_start.isoformat(),
                "signal_bar_end": event_time.isoformat(),
                "event_timestamp": event_time.isoformat(),
                "direction": "BULLISH" if bullish_side else "BEARISH",
                "right": "CE" if bullish_side else "PE",
                "signal_close": float(row.close),
                "macd": float(row.macd),
                "macd_signal": float(row.signal),
                "expiry": _nearest_expiry(day).isoformat(),
                "strike": _atm_strike(float(row.close)),
            }
        )
    return events


def _trade_intents(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_day[str(event["date"])].append(event)

    force_time = time.fromisoformat(BACKTEST_WINDOW["force_exit_time"])
    intents: list[dict[str, Any]] = []

    for day, day_events in sorted(by_day.items()):
        current: dict[str, Any] | None = None
        for event in sorted(day_events, key=lambda x: x["event_timestamp"]):
            event_dt = datetime.fromisoformat(str(event["event_timestamp"]))
            if event_dt.time() >= force_time:
                if current is not None:
                    intents.append(
                        {
                            **current,
                            "exit_timestamp": datetime.combine(
                                event_dt.date(),
                                force_time,
                                tzinfo=event_dt.tzinfo,
                            ).isoformat(),
                            "exit_reason": "FORCE_EXIT_OR_CROSS_AT_15_20",
                        }
                    )
                    current = None
                break

            if current is None:
                current = {
                    "date": day,
                    "direction": event["direction"],
                    "right": event["right"],
                    "expiry": event["expiry"],
                    "strike": int(event["strike"]),
                    "signal_close": float(event["signal_close"]),
                    "signal_bar_start": event["signal_bar_start"],
                    "entry_timestamp": event["event_timestamp"],
                    "entry_macd": float(event["macd"]),
                    "entry_macd_signal": float(event["macd_signal"]),
                }
                continue

            if event["direction"] == current["direction"]:
                continue

            intents.append(
                {
                    **current,
                    "exit_timestamp": event["event_timestamp"],
                    "exit_reason": "OPPOSITE_MACD_CROSSOVER",
                }
            )
            current = {
                "date": day,
                "direction": event["direction"],
                "right": event["right"],
                "expiry": event["expiry"],
                "strike": int(event["strike"]),
                "signal_close": float(event["signal_close"]),
                "signal_bar_start": event["signal_bar_start"],
                "entry_timestamp": event["event_timestamp"],
                "entry_macd": float(event["macd"]),
                "entry_macd_signal": float(event["macd_signal"]),
            }

        if current is not None:
            entry_dt = datetime.fromisoformat(str(current["entry_timestamp"]))
            intents.append(
                {
                    **current,
                    "exit_timestamp": datetime.combine(
                        entry_dt.date(),
                        force_time,
                        tzinfo=entry_dt.tzinfo,
                    ).isoformat(),
                    "exit_reason": "FORCE_EXIT_15_20",
                }
            )
    return intents


def _option_lookup(option_rows: list[dict[str, Any]]) -> dict[tuple[str, str, int, str], dict[str, Any]]:
    lookup: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for row in option_rows:
        key = (
            str(row["timestamp"]),
            str(row["expiry"]),
            int(row["strike"]),
            str(row["right"]),
        )
        lookup[key] = row
    return lookup


def _transaction_costs(
    entry_open: float,
    exit_open: float,
    *,
    slippage_points: float,
) -> dict[str, float]:
    qty = int(OPTION_SELECTION["lot_size"])
    entry_fill = entry_open + slippage_points
    exit_fill = max(0.0, exit_open - slippage_points)
    buy_turnover = entry_fill * qty
    sell_turnover = exit_fill * qty
    turnover = buy_turnover + sell_turnover

    brokerage = 2.0 * float(COST_MODEL["brokerage_per_order_inr"])
    exchange = turnover * float(COST_MODEL["exchange_charge_rate"])
    stt = sell_turnover * float(COST_MODEL["stt_sell_rate"])
    sebi = turnover * float(COST_MODEL["sebi_charge_rate"])
    stamp = buy_turnover * float(COST_MODEL["stamp_buy_rate"])
    gst = (
        brokerage + exchange + sebi
    ) * float(COST_MODEL["gst_rate"])
    explicit_costs = brokerage + exchange + stt + sebi + stamp + gst
    gross = (exit_fill - entry_fill) * qty
    net = gross - explicit_costs
    return {
        "entry_fill": round(entry_fill, 2),
        "exit_fill": round(exit_fill, 2),
        "gross_pnl_inr": round(gross, 2),
        "brokerage_inr": round(brokerage, 2),
        "exchange_charges_inr": round(exchange, 2),
        "stt_inr": round(stt, 2),
        "sebi_charges_inr": round(sebi, 2),
        "stamp_duty_inr": round(stamp, 2),
        "gst_inr": round(gst, 2),
        "explicit_costs_inr": round(explicit_costs, 2),
        "net_pnl_inr": round(net, 2),
    }


def _price_intents(
    intents: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lookup = _option_lookup(option_rows)
    scored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    primary_slip = float(COST_MODEL["primary_slippage_points_each_side"])
    sensitivity = [
        float(x) for x in COST_MODEL["slippage_sensitivity_points_each_side"]
    ]

    for idx, intent in enumerate(intents, start=1):
        contract = (
            str(intent["expiry"]),
            int(intent["strike"]),
            str(intent["right"]),
        )
        entry_key = (
            str(intent["entry_timestamp"]),
            contract[0],
            contract[1],
            contract[2],
        )
        exit_key = (
            str(intent["exit_timestamp"]),
            contract[0],
            contract[1],
            contract[2],
        )
        entry_row = lookup.get(entry_key)
        exit_row = lookup.get(exit_key)
        missing = []
        if entry_row is None:
            missing.append("ENTRY_OPTION_BAR")
        if exit_row is None:
            missing.append("EXIT_OPTION_BAR")
        if missing:
            skipped.append(
                {
                    "trade_number": idx,
                    **intent,
                    "skip_reasons": missing,
                }
            )
            continue

        entry_open = float(entry_row["open"])
        exit_open = float(exit_row["open"])
        if not (
            math.isfinite(entry_open)
            and math.isfinite(exit_open)
            and entry_open > 0
            and exit_open >= 0
        ):
            skipped.append(
                {
                    "trade_number": idx,
                    **intent,
                    "skip_reasons": ["INVALID_OPTION_OPEN"],
                }
            )
            continue

        primary = _transaction_costs(
            entry_open,
            exit_open,
            slippage_points=primary_slip,
        )
        sensitivity_results = {
            f"{slip:.2f}": _transaction_costs(
                entry_open,
                exit_open,
                slippage_points=slip,
            )
            for slip in sensitivity
        }
        entry_dt = datetime.fromisoformat(str(intent["entry_timestamp"]))
        exit_dt = datetime.fromisoformat(str(intent["exit_timestamp"]))
        dte = (date.fromisoformat(contract[0]) - entry_dt.date()).days
        premium_points = exit_open - entry_open
        return_pct = premium_points / entry_open * 100.0

        scored.append(
            {
                "trade_number": idx,
                **intent,
                "entry_option_open": entry_open,
                "exit_option_open": exit_open,
                "premium_points": round(premium_points, 2),
                "premium_return_pct_gross_before_costs": round(return_pct, 4),
                "hold_minutes": int((exit_dt - entry_dt).total_seconds() // 60),
                "dte_calendar_days": dte,
                "lot_size": int(OPTION_SELECTION["lot_size"]),
                "primary_cost_model": primary,
                "slippage_sensitivity": sensitivity_results,
            }
        )
    return scored, skipped


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses == 0:
        return None if wins == 0 else float("inf")
    return wins / losses


def _max_drawdown(values: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd


def _summarize_trade_group(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "total_net_pnl_inr": 0.0,
        }
    net = [float(t["primary_cost_model"]["net_pnl_inr"]) for t in trades]
    gross = [float(t["primary_cost_model"]["gross_pnl_inr"]) for t in trades]
    returns = [float(t["premium_return_pct_gross_before_costs"]) for t in trades]
    wins = sum(v > 0 for v in net)
    losses = sum(v < 0 for v in net)
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "breakeven": len(trades) - wins - losses,
        "win_rate_pct": round(wins / len(trades) * 100.0, 2),
        "total_gross_pnl_inr": round(sum(gross), 2),
        "total_net_pnl_inr": round(sum(net), 2),
        "average_net_pnl_inr": round(sum(net) / len(net), 2),
        "median_net_pnl_inr": round(float(pd.Series(net).median()), 2),
        "average_gross_premium_return_pct": round(sum(returns) / len(returns), 4),
        "profit_factor": (
            None
            if _profit_factor(net) is None
            else (
                "INF"
                if math.isinf(float(_profit_factor(net)))
                else round(float(_profit_factor(net)), 4)
            )
        ),
        "max_drawdown_inr": round(_max_drawdown(net), 2),
        "largest_win_inr": round(max(net), 2),
        "largest_loss_inr": round(min(net), 2),
        "average_hold_minutes": round(
            sum(int(t["hold_minutes"]) for t in trades) / len(trades),
            2,
        ),
    }


def _time_bucket(ts: str) -> str:
    value = datetime.fromisoformat(ts).time()
    if value < time(10, 30):
        return "09:20_10:25"
    if value < time(12, 0):
        return "10:30_11:55"
    if value < time(13, 30):
        return "12:00_13:25"
    return "13:30_15:15"


def _slippage_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for slip in COST_MODEL["slippage_sensitivity_points_each_side"]:
        key = f"{float(slip):.2f}"
        values = [
            float(t["slippage_sensitivity"][key]["net_pnl_inr"])
            for t in trades
        ]
        result[key] = {
            "slippage_points_each_side": float(slip),
            "total_net_pnl_inr": round(sum(values), 2),
            "average_net_pnl_inr": (
                round(sum(values) / len(values), 2) if values else None
            ),
            "win_rate_pct": (
                round(sum(v > 0 for v in values) / len(values) * 100.0, 2)
                if values
                else None
            ),
            "profit_factor": (
                None
                if not values or _profit_factor(values) is None
                else (
                    "INF"
                    if math.isinf(float(_profit_factor(values)))
                    else round(float(_profit_factor(values)), 4)
                )
            ),
        }
    return result


def backtest(payload: dict[str, Any], *, source_sha256: str | None = None) -> dict[str, Any]:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Strategy F market artifact protocol mismatch")
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("Strategy F source artifact must be outcome-unscored")

    spot_rows = list(payload.get("spot_rows") or [])
    option_rows = list(payload.get("option_rows") or [])
    complete_days, partial_days = _complete_backtest_days(spot_rows)
    events = _all_crossover_events(spot_rows, complete_days)
    intents = _trade_intents(events)
    trades, skipped = _price_intents(intents, option_rows)

    calls = [t for t in trades if t["right"] == "CE"]
    puts = [t for t in trades if t["right"] == "PE"]

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_day[str(trade["date"])].append(trade)
        by_time[_time_bucket(str(trade["entry_timestamp"]))].append(trade)

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "backtest_window": BACKTEST_WINDOW,
        "rules": {
            "macd": MACD,
            "signal": SIGNAL,
            "option_selection": OPTION_SELECTION,
            "execution": EXECUTION,
            "cost_model": COST_MODEL,
        },
        "quality": {
            "complete_spot_sessions_in_window": len(complete_days),
            "partial_spot_sessions_in_window": partial_days,
            "crossover_events": len(events),
            "intended_trades": len(intents),
            "scorable_trades": len(trades),
            "skipped_trades": len(skipped),
            "trade_price_coverage_pct": (
                round(len(trades) / len(intents) * 100.0, 2)
                if intents
                else None
            ),
            "fidelity": COST_MODEL["fidelity"],
        },
        "summary": _summarize_trade_group(trades),
        "call_summary": _summarize_trade_group(calls),
        "put_summary": _summarize_trade_group(puts),
        "daily_summary": {
            day: _summarize_trade_group(group)
            for day, group in sorted(by_day.items())
        },
        "time_of_day_summary": {
            bucket: _summarize_trade_group(group)
            for bucket, group in sorted(by_time.items())
        },
        "slippage_sensitivity": _slippage_summary(trades),
        "trades": trades,
        "skipped_trade_intents": skipped,
        "reporting_contract": REPORTING,
        "guardrails": {
            "research_only": True,
            "backtest_only": True,
            "live_execution": False,
            "paper_execution": False,
            "broker_called": False,
            "no_parameter_optimization": True,
            "no_posthoc_signal_filters": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest Strategy F MACD Call/Put crossover for September 2026"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f_macd_options_backtest_2026_09.json"),
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
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "source_market_sha256": source_sha,
                "quality": report["quality"],
                "summary": report["summary"],
                "call_summary": report["call_summary"],
                "put_summary": report["put_summary"],
                "slippage_sensitivity": report["slippage_sensitivity"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
