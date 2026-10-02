"""Backtest frozen June PE-only Strategy F3 option-native MACD."""
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

from services.historical.strategy_f3_pe_june_replication_protocol import (
    BACKTEST_WINDOW,
    COST_MODEL,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    REPLICATION_GATE,
    STRATEGY_ID,
    STRATEGY_NAME,
)

RESEARCH_TYPE = "STRATEGY_F3_PE_JUNE_REPLICATION_BACKTEST_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _macd_by_contract(
    option_rows: list[dict[str, Any]],
) -> dict[tuple[str, int], pd.DataFrame]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in option_rows:
        if str(row["right"]) != "PE":
            raise ValueError("PE-only source artifact contains non-PE row")
        grouped[(str(row["expiry"]), int(row["strike"]))].append(row)

    result: dict[tuple[str, int], pd.DataFrame] = {}
    for key, rows in grouped.items():
        frame = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        close = frame["close"].astype(float)
        fast = close.ewm(span=int(MACD["fast_length"]), adjust=False).mean()
        slow = close.ewm(span=int(MACD["slow_length"]), adjust=False).mean()
        frame["macd"] = fast - slow
        frame["signal"] = frame["macd"].ewm(
            span=int(MACD["signal_length"]), adjust=False
        ).mean()
        frame["macd_prev"] = frame["macd"].shift(1)
        frame["signal_prev"] = frame["signal"].shift(1)
        result[key] = frame
    return result


def _signals(
    daily_contracts: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frames = _macd_by_contract(option_rows)
    min_prior = int(MACD["minimum_prior_option_bars_before_session"])
    force_exit = time.fromisoformat(BACKTEST_WINDOW["force_exit_time"])
    events: list[dict[str, Any]] = []
    insufficient: list[dict[str, Any]] = []

    for selected in daily_contracts:
        day = date.fromisoformat(str(selected["date"]))
        key = (str(selected["expiry"]), int(selected["strike"]))
        frame = frames.get(key)
        if frame is None or frame.empty:
            insufficient.append({
                "date": day.isoformat(),
                "expiry": key[0],
                "strike": key[1],
                "reason": "NO_PE_ROWS",
            })
            continue

        indices = list(frame.index[frame["timestamp"].dt.date == day])
        if not indices:
            insufficient.append({
                "date": day.isoformat(),
                "expiry": key[0],
                "strike": key[1],
                "reason": "NO_SESSION_ROWS",
            })
            continue

        prior_bars = int(indices[0])
        if prior_bars < min_prior:
            insufficient.append({
                "date": day.isoformat(),
                "expiry": key[0],
                "strike": key[1],
                "reason": "INSUFFICIENT_MACD_WARMUP",
                "prior_bars": prior_bars,
                "required_prior_bars": min_prior,
            })
            continue

        for idx in indices:
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
            if event_time.time() > force_exit:
                continue
            events.append({
                "date": day.isoformat(),
                "event_timestamp": event_time.isoformat(),
                "bar_start": bar_start.isoformat(),
                "expiry": key[0],
                "strike": key[1],
                "right": "PE",
                "event": "BULLISH_CROSS" if bullish else "BEARISH_CROSS",
                "option_close": float(row["close"]),
                "macd": float(row["macd"]),
                "macd_signal": float(row["signal"]),
            })
    events.sort(key=lambda x: x["event_timestamp"])
    return events, insufficient


def _trade_intents(
    daily_contracts: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    force_exit = time.fromisoformat(BACKTEST_WINDOW["force_exit_time"])
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_day[str(event["date"])].append(event)

    selected_by_day = {str(x["date"]): x for x in daily_contracts}
    intents: list[dict[str, Any]] = []

    for day, selected in sorted(selected_by_day.items()):
        current: dict[str, Any] | None = None
        for event in sorted(by_day.get(day, []), key=lambda x: x["event_timestamp"]):
            event_dt = datetime.fromisoformat(str(event["event_timestamp"]))

            if current is not None and event["event"] == "BEARISH_CROSS":
                intents.append({
                    **current,
                    "exit_timestamp": event["event_timestamp"],
                    "exit_reason": "PE_BEARISH_MACD_CROSS",
                    "exit_macd": float(event["macd"]),
                    "exit_macd_signal": float(event["macd_signal"]),
                })
                current = None
                continue

            if current is not None:
                continue
            if event_dt.time() >= force_exit:
                continue
            if event["event"] != "BULLISH_CROSS":
                continue

            current = {
                "date": day,
                "entry_timestamp": event["event_timestamp"],
                "right": "PE",
                "expiry": str(event["expiry"]),
                "strike": int(event["strike"]),
                "entry_option_signal_close": float(event["option_close"]),
                "entry_macd": float(event["macd"]),
                "entry_macd_signal": float(event["macd_signal"]),
                "daily_spot_0915_open": float(selected["spot_0915_open"]),
            }

        if current is not None:
            entry_dt = datetime.fromisoformat(str(current["entry_timestamp"]))
            force_dt = datetime.combine(
                entry_dt.date(),
                force_exit,
                tzinfo=entry_dt.tzinfo,
            )
            intents.append({
                **current,
                "exit_timestamp": force_dt.isoformat(),
                "exit_reason": "FORCE_EXIT_15_20",
                "exit_macd": None,
                "exit_macd_signal": None,
            })
    return intents


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


def _score(
    intents: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lookup = {
        (
            str(row["timestamp"]),
            str(row["expiry"]),
            int(row["strike"]),
        ): row
        for row in option_rows
    }
    slips = [float(x) for x in COST_MODEL["slippage_sensitivity_points_each_side"]]
    trades: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for n, intent in enumerate(intents, start=1):
        base = (str(intent["expiry"]), int(intent["strike"]))
        entry_row = lookup.get((str(intent["entry_timestamp"]), *base))
        exit_row = lookup.get((str(intent["exit_timestamp"]), *base))
        missing = []
        if entry_row is None:
            missing.append("ENTRY_PE_BAR")
        if exit_row is None:
            missing.append("EXIT_PE_BAR")
        if missing:
            skipped.append({"trade_number": n, **intent, "skip_reasons": missing})
            continue

        entry_open = float(entry_row["open"])
        exit_open = float(exit_row["open"])
        if not (
            math.isfinite(entry_open)
            and math.isfinite(exit_open)
            and entry_open > 0
            and exit_open >= 0
        ):
            skipped.append({
                "trade_number": n,
                **intent,
                "skip_reasons": ["INVALID_PE_OPEN"],
            })
            continue

        entry_dt = datetime.fromisoformat(str(intent["entry_timestamp"]))
        exit_dt = datetime.fromisoformat(str(intent["exit_timestamp"]))
        trades.append({
            "trade_number": n,
            **intent,
            "entry_option_open": entry_open,
            "exit_option_open": exit_open,
            "premium_return_pct_gross_before_costs": round(
                (exit_open - entry_open) / entry_open * 100.0, 4
            ),
            "hold_minutes": int((exit_dt - entry_dt).total_seconds() // 60),
            "lot_size": int(OPTION_SELECTION["lot_size"]),
            "primary_cost_model": _costs(entry_open, exit_open, 0.0),
            "slippage_sensitivity": {
                f"{slip:.2f}": _costs(entry_open, exit_open, slip)
                for slip in slips
            },
        })
    return trades, skipped


def _pf(values: list[float]) -> float | None:
    pos = sum(v for v in values if v > 0)
    neg = -sum(v for v in values if v < 0)
    if neg == 0:
        return None if pos == 0 else float("inf")
    return pos / neg


def _max_dd(values: list[float]) -> float:
    equity = peak = max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "total_net_pnl_inr": 0.0, "profit_factor": None}
    net = [float(t["primary_cost_model"]["net_pnl_inr"]) for t in trades]
    gross = [float(t["primary_cost_model"]["gross_pnl_inr"]) for t in trades]
    pf = _pf(net)
    return {
        "trades": len(trades),
        "wins": sum(v > 0 for v in net),
        "losses": sum(v < 0 for v in net),
        "win_rate_pct": round(sum(v > 0 for v in net) / len(net) * 100.0, 2),
        "total_gross_pnl_inr": round(sum(gross), 2),
        "total_net_pnl_inr": round(sum(net), 2),
        "average_net_pnl_inr": round(sum(net) / len(net), 2),
        "median_net_pnl_inr": round(float(pd.Series(net).median()), 2),
        "profit_factor": None if pf is None else round(float(pf), 4),
        "max_drawdown_inr": round(_max_dd(net), 2),
        "largest_win_inr": round(max(net), 2),
        "largest_loss_inr": round(min(net), 2),
        "average_hold_minutes": round(
            sum(int(t["hold_minutes"]) for t in trades) / len(trades), 2
        ),
    }


def _slippage(trades: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for slip in COST_MODEL["slippage_sensitivity_points_each_side"]:
        key = f"{float(slip):.2f}"
        vals = [float(t["slippage_sensitivity"][key]["net_pnl_inr"]) for t in trades]
        pf = _pf(vals) if vals else None
        out[key] = {
            "slippage_points_each_side": float(slip),
            "total_net_pnl_inr": round(sum(vals), 2),
            "profit_factor": None if pf is None else round(float(pf), 4),
            "win_rate_pct": (
                round(sum(v > 0 for v in vals) / len(vals) * 100.0, 2)
                if vals else None
            ),
        }
    return out


def backtest(payload: dict[str, Any], *, source_sha256: str | None = None) -> dict[str, Any]:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("June PE market artifact protocol mismatch")
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("June PE market artifact must be outcome-unscored")

    daily_contracts = list(payload.get("daily_contracts") or [])
    option_rows = list(payload.get("option_rows") or [])
    events, insufficient = _signals(daily_contracts, option_rows)
    intents = _trade_intents(daily_contracts, events)
    trades, skipped = _score(intents, option_rows)
    slips = _slippage(trades)
    summary = _summary(trades)

    failures: list[str] = []
    if summary.get("total_net_pnl_inr", 0.0) <= 0:
        failures.append("net_pnl_not_positive")
    pf = summary.get("profit_factor")
    if pf is None or float(pf) <= 1.0:
        failures.append("profit_factor_not_above_one")
    if slips["0.50"]["total_net_pnl_inr"] <= 0:
        failures.append("half_point_slippage_net_not_positive")
    if slips["1.00"]["total_net_pnl_inr"] <= 0:
        failures.append("one_point_slippage_net_not_positive")
    coverage = len(trades) / len(intents) * 100.0 if intents else 0.0
    if coverage < 100.0:
        failures.append("trade_price_coverage_not_full")

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "backtest_window": BACKTEST_WINDOW,
        "quality": {
            "selected_sessions": len(daily_contracts),
            "pe_macd_cross_events": len(events),
            "insufficient_warmup_sessions": len(insufficient),
            "intended_trades": len(intents),
            "scorable_trades": len(trades),
            "skipped_trades": len(skipped),
            "trade_price_coverage_pct": round(coverage, 2) if intents else None,
            "fidelity": COST_MODEL["fidelity"],
        },
        "summary": summary,
        "slippage_sensitivity": slips,
        "replication_gate": {
            "passed": not failures,
            "failures": failures,
            "contract": REPLICATION_GATE,
        },
        "insufficient_warmup": insufficient,
        "trades": trades,
        "skipped_trade_intents": skipped,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest frozen June PE-only Strategy F3 replication"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f3_pe_june_replication_backtest_2026_06.json"),
    )
    args = parser.parse_args()
    source_sha = _sha256(args.market)
    payload = json.loads(args.market.read_text(encoding="utf-8"))
    report = backtest(payload, source_sha256=source_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_market_sha256": source_sha,
        "quality": report["quality"],
        "summary": report["summary"],
        "slippage_sensitivity": report["slippage_sensitivity"],
        "replication_gate": report["replication_gate"],
    }, indent=2))


if __name__ == "__main__":
    main()
