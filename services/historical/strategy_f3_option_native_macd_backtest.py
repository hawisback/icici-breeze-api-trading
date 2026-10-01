"""Backtest Strategy F3 using each option contract's own MACD(12,26,9)."""
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

from services.historical.strategy_f3_option_native_macd_protocol import (
    BACKTEST_WINDOW,
    COST_MODEL,
    EXECUTION,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    REPORTING,
    STRATEGY_ID,
    STRATEGY_NAME,
)

RESEARCH_TYPE = "STRATEGY_F3_OPTION_NATIVE_MACD_BACKTEST_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contract_key(row: dict[str, Any]) -> tuple[str, int, str]:
    return str(row["expiry"]), int(row["strike"]), str(row["right"])


def _macd_by_contract(
    option_rows: list[dict[str, Any]],
) -> dict[tuple[str, int, str], pd.DataFrame]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in option_rows:
        grouped[_contract_key(row)].append(row)

    result: dict[tuple[str, int, str], pd.DataFrame] = {}
    for key, rows in grouped.items():
        frame = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        close = frame["close"].astype(float)
        fast = close.ewm(span=int(MACD["fast_length"]), adjust=False).mean()
        slow = close.ewm(span=int(MACD["slow_length"]), adjust=False).mean()
        frame["macd"] = fast - slow
        frame["signal"] = frame["macd"].ewm(
            span=int(MACD["signal_length"]),
            adjust=False,
        ).mean()
        frame["macd_prev"] = frame["macd"].shift(1)
        frame["signal_prev"] = frame["signal"].shift(1)
        result[key] = frame
    return result


def _daily_signal_events(
    daily_contracts: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frames = _macd_by_contract(option_rows)
    force_exit = time.fromisoformat(BACKTEST_WINDOW["force_exit_time"])
    min_prior = int(MACD["minimum_prior_option_bars_before_session"])
    events: list[dict[str, Any]] = []
    insufficient: list[dict[str, Any]] = []

    for selected in daily_contracts:
        day = date.fromisoformat(str(selected["date"]))
        for right in ("CE", "PE"):
            key = (
                str(selected["expiry"]),
                int(selected["strike"]),
                right,
            )
            frame = frames.get(key)
            if frame is None or frame.empty:
                insufficient.append(
                    {
                        "date": day.isoformat(),
                        "expiry": key[0],
                        "strike": key[1],
                        "right": right,
                        "reason": "NO_OPTION_ROWS",
                    }
                )
                continue

            session_mask = frame["timestamp"].dt.date == day
            session_indices = list(frame.index[session_mask])
            if not session_indices:
                insufficient.append(
                    {
                        "date": day.isoformat(),
                        "expiry": key[0],
                        "strike": key[1],
                        "right": right,
                        "reason": "NO_SESSION_ROWS",
                    }
                )
                continue

            first_idx = session_indices[0]
            prior_bars = int(first_idx)
            if prior_bars < min_prior:
                insufficient.append(
                    {
                        "date": day.isoformat(),
                        "expiry": key[0],
                        "strike": key[1],
                        "right": right,
                        "reason": "INSUFFICIENT_MACD_WARMUP",
                        "prior_bars": prior_bars,
                        "required_prior_bars": min_prior,
                    }
                )
                continue

            for idx in session_indices:
                if idx <= 0:
                    continue
                row = frame.loc[idx]
                if pd.isna(row["macd_prev"]) or pd.isna(row["signal_prev"]):
                    continue
                bullish = (
                    float(row["macd_prev"]) <= float(row["signal_prev"])
                    and float(row["macd"]) > float(row["signal"])
                )
                bearish = (
                    float(row["macd_prev"]) >= float(row["signal_prev"])
                    and float(row["macd"]) < float(row["signal"])
                )
                if not (bullish or bearish):
                    continue

                bar_start = pd.Timestamp(row["timestamp"]).to_pydatetime()
                event_time = bar_start + timedelta(minutes=5)
                # Bearish event at 15:20 may still exit. Bullish entry at/after
                # 15:20 is filtered later by the execution state machine.
                if event_time.time() > force_exit:
                    continue
                events.append(
                    {
                        "date": day.isoformat(),
                        "event_timestamp": event_time.isoformat(),
                        "bar_start": bar_start.isoformat(),
                        "expiry": key[0],
                        "strike": key[1],
                        "right": right,
                        "event": "BULLISH_CROSS" if bullish else "BEARISH_CROSS",
                        "option_close": float(row["close"]),
                        "macd": float(row["macd"]),
                        "macd_signal": float(row["signal"]),
                    }
                )
    events.sort(
        key=lambda x: (
            x["event_timestamp"],
            x["right"],
            x["event"],
        )
    )
    return events, insufficient


def _build_trade_intents(
    daily_contracts: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    force_exit = time.fromisoformat(BACKTEST_WINDOW["force_exit_time"])
    selected_by_day = {
        str(row["date"]): row
        for row in daily_contracts
    }
    events_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_day[str(event["date"])].append(event)

    intents: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    for day, selected in sorted(selected_by_day.items()):
        day_events = events_by_day.get(day, [])
        timestamps = sorted({str(x["event_timestamp"]) for x in day_events})
        current: dict[str, Any] | None = None

        for ts in timestamps:
            event_dt = datetime.fromisoformat(ts)
            same = [x for x in day_events if x["event_timestamp"] == ts]

            # Exit first on the held option's own bearish crossover.
            if current is not None:
                exit_event = next(
                    (
                        x for x in same
                        if x["right"] == current["right"]
                        and x["event"] == "BEARISH_CROSS"
                    ),
                    None,
                )
                if exit_event is not None:
                    intents.append(
                        {
                            **current,
                            "exit_timestamp": ts,
                            "exit_reason": "HELD_OPTION_BEARISH_MACD_CROSS",
                            "exit_macd": float(exit_event["macd"]),
                            "exit_macd_signal": float(exit_event["macd_signal"]),
                        }
                    )
                    current = None

            if event_dt.time() >= force_exit:
                continue
            if current is not None:
                continue

            bullish = [x for x in same if x["event"] == "BULLISH_CROSS"]
            if len(bullish) > 1:
                ambiguous.append(
                    {
                        "date": day,
                        "event_timestamp": ts,
                        "reason": "SIMULTANEOUS_CE_PE_BULLISH_CROSS",
                        "candidates": bullish,
                    }
                )
                continue
            if len(bullish) != 1:
                continue

            entry = bullish[0]
            current = {
                "date": day,
                "entry_timestamp": ts,
                "right": str(entry["right"]),
                "expiry": str(entry["expiry"]),
                "strike": int(entry["strike"]),
                "entry_option_signal_close": float(entry["option_close"]),
                "entry_macd": float(entry["macd"]),
                "entry_macd_signal": float(entry["macd_signal"]),
                "daily_spot_0915_open": float(selected["spot_0915_open"]),
            }

        if current is not None:
            entry_dt = datetime.fromisoformat(str(current["entry_timestamp"]))
            force_dt = datetime.combine(
                entry_dt.date(),
                force_exit,
                tzinfo=entry_dt.tzinfo,
            )
            intents.append(
                {
                    **current,
                    "exit_timestamp": force_dt.isoformat(),
                    "exit_reason": "FORCE_EXIT_15_20",
                    "exit_macd": None,
                    "exit_macd_signal": None,
                }
            )

    return intents, ambiguous


def _option_lookup(
    option_rows: list[dict[str, Any]],
) -> dict[tuple[str, str, int, str], dict[str, Any]]:
    return {
        (
            str(row["timestamp"]),
            str(row["expiry"]),
            int(row["strike"]),
            str(row["right"]),
        ): row
        for row in option_rows
    }


def _costs(entry_open: float, exit_open: float, slippage: float) -> dict[str, float]:
    qty = int(OPTION_SELECTION["lot_size"])
    entry_fill = entry_open + slippage
    exit_fill = max(0.0, exit_open - slippage)
    buy_turnover = entry_fill * qty
    sell_turnover = exit_fill * qty
    turnover = buy_turnover + sell_turnover

    brokerage = 2.0 * float(COST_MODEL["brokerage_per_order_inr"])
    exchange = turnover * float(COST_MODEL["exchange_charge_rate"])
    stt = sell_turnover * float(COST_MODEL["stt_sell_rate"])
    sebi = turnover * float(COST_MODEL["sebi_charge_rate"])
    stamp = buy_turnover * float(COST_MODEL["stamp_buy_rate"])
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


def _score(
    intents: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lookup = _option_lookup(option_rows)
    primary_slip = float(COST_MODEL["primary_slippage_points_each_side"])
    sensitivity = [
        float(x) for x in COST_MODEL["slippage_sensitivity_points_each_side"]
    ]
    trades: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for n, intent in enumerate(intents, start=1):
        key_base = (
            str(intent["expiry"]),
            int(intent["strike"]),
            str(intent["right"]),
        )
        entry_key = (
            str(intent["entry_timestamp"]),
            key_base[0],
            key_base[1],
            key_base[2],
        )
        exit_key = (
            str(intent["exit_timestamp"]),
            key_base[0],
            key_base[1],
            key_base[2],
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
                    "trade_number": n,
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
                    "trade_number": n,
                    **intent,
                    "skip_reasons": ["INVALID_OPTION_OPEN"],
                }
            )
            continue

        entry_dt = datetime.fromisoformat(str(intent["entry_timestamp"]))
        exit_dt = datetime.fromisoformat(str(intent["exit_timestamp"]))
        primary = _costs(entry_open, exit_open, primary_slip)
        trades.append(
            {
                "trade_number": n,
                **intent,
                "entry_option_open": entry_open,
                "exit_option_open": exit_open,
                "premium_return_pct_gross_before_costs": round(
                    (exit_open - entry_open) / entry_open * 100.0,
                    4,
                ),
                "hold_minutes": int((exit_dt - entry_dt).total_seconds() // 60),
                "lot_size": int(OPTION_SELECTION["lot_size"]),
                "primary_cost_model": primary,
                "slippage_sensitivity": {
                    f"{slip:.2f}": _costs(entry_open, exit_open, slip)
                    for slip in sensitivity
                },
            }
        )
    return trades, skipped


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses == 0:
        return None if wins == 0 else float("inf")
    return wins / losses


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "total_net_pnl_inr": 0.0,
            "profit_factor": None,
            "largest_winner_share_of_positive_net_pct": None,
        }

    net = [float(t["primary_cost_model"]["net_pnl_inr"]) for t in trades]
    gross = [float(t["primary_cost_model"]["gross_pnl_inr"]) for t in trades]
    pf = _profit_factor(net)
    wins = [v for v in net if v > 0]
    largest = max(net)
    total_net = sum(net)
    concentration = (
        largest / total_net * 100.0
        if largest > 0 and total_net > 0
        else None
    )
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": sum(v < 0 for v in net),
        "breakeven": sum(v == 0 for v in net),
        "win_rate_pct": round(len(wins) / len(net) * 100.0, 2),
        "total_gross_pnl_inr": round(sum(gross), 2),
        "total_net_pnl_inr": round(total_net, 2),
        "average_net_pnl_inr": round(total_net / len(net), 2),
        "median_net_pnl_inr": round(float(pd.Series(net).median()), 2),
        "profit_factor": (
            None if pf is None else ("INF" if math.isinf(pf) else round(pf, 4))
        ),
        "max_drawdown_inr": round(_max_drawdown(net), 2),
        "largest_win_inr": round(max(net), 2),
        "largest_loss_inr": round(min(net), 2),
        "average_hold_minutes": round(
            sum(int(t["hold_minutes"]) for t in trades) / len(trades),
            2,
        ),
        "largest_winner_share_of_positive_net_pct": (
            round(concentration, 2) if concentration is not None else None
        ),
    }


def _slippage_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for slip in COST_MODEL["slippage_sensitivity_points_each_side"]:
        key = f"{float(slip):.2f}"
        values = [
            float(t["slippage_sensitivity"][key]["net_pnl_inr"])
            for t in trades
        ]
        pf = _profit_factor(values) if values else None
        result[key] = {
            "slippage_points_each_side": float(slip),
            "total_net_pnl_inr": round(sum(values), 2),
            "win_rate_pct": (
                round(sum(v > 0 for v in values) / len(values) * 100.0, 2)
                if values
                else None
            ),
            "profit_factor": (
                None
                if pf is None
                else ("INF" if math.isinf(pf) else round(pf, 4))
            ),
        }
    return result


def _time_bucket(ts: str) -> str:
    value = datetime.fromisoformat(ts).time()
    if value < time(10, 30):
        return "09:20_10:25"
    if value < time(12, 0):
        return "10:30_11:55"
    if value < time(13, 30):
        return "12:00_13:25"
    return "13:30_15:15"


def backtest(
    payload: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("F3 market artifact protocol mismatch")
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("F3 market artifact must be outcome-unscored")

    daily_contracts = list(payload.get("daily_contracts") or [])
    option_rows = list(payload.get("option_rows") or [])
    events, insufficient = _daily_signal_events(daily_contracts, option_rows)
    intents, ambiguous = _build_trade_intents(daily_contracts, events)
    trades, skipped = _score(intents, option_rows)

    ce = [t for t in trades if t["right"] == "CE"]
    pe = [t for t in trades if t["right"] == "PE"]
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
            "option_selection": OPTION_SELECTION,
            "execution": EXECUTION,
            "cost_model": COST_MODEL,
        },
        "quality": {
            "selected_sessions": len(daily_contracts),
            "option_macd_cross_events": len(events),
            "insufficient_warmup_side_sessions": len(insufficient),
            "ambiguous_simultaneous_entry_timestamps": len(ambiguous),
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
        "summary": _summary(trades),
        "ce_summary": _summary(ce),
        "pe_summary": _summary(pe),
        "daily_summary": {
            day: _summary(group)
            for day, group in sorted(by_day.items())
        },
        "time_of_day_summary": {
            bucket: _summary(group)
            for bucket, group in sorted(by_time.items())
        },
        "slippage_sensitivity": _slippage_summary(trades),
        "insufficient_warmup": insufficient,
        "ambiguous_entry_timestamps": ambiguous,
        "trades": trades,
        "skipped_trade_intents": skipped,
        "reporting_contract": REPORTING,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest September 2026 Strategy F3 option-native MACD"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f3_option_native_macd_backtest_2026_09.json"),
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
                "ce_summary": report["ce_summary"],
                "pe_summary": report["pe_summary"],
                "slippage_sensitivity": report["slippage_sensitivity"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
