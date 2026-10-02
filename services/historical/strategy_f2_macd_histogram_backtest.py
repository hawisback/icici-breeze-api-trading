"""Backtest the frozen Strategy F2 histogram-persistence variants."""
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

from services.historical.strategy_f2_macd_histogram_market import (
    _macd_frame,
    _qualified_entries,
)
from services.historical.strategy_f2_macd_histogram_protocol import (
    BACKTEST_WINDOW,
    COST_MODEL,
    EXECUTION,
    GUARDRAILS,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    STRATEGY_NAME,
    VARIANTS,
)

RESEARCH_TYPE = "STRATEGY_F2_MACD_HISTOGRAM_BACKTEST_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _complete_days(spot_rows: list[dict[str, Any]]) -> set[str]:
    start = date.fromisoformat(BACKTEST_WINDOW["start"])
    end = date.fromisoformat(BACKTEST_WINDOW["end"])
    counts: dict[str, int] = defaultdict(int)
    for row in spot_rows:
        day = date.fromisoformat(str(row["date"]))
        if start <= day <= end:
            counts[day.isoformat()] += 1
    return {day for day, count in counts.items() if count == 75}


def _raw_crossovers(
    spot_rows: list[dict[str, Any]],
    complete_days: set[str],
) -> list[dict[str, Any]]:
    frame = _macd_frame(spot_rows)
    start = date.fromisoformat(BACKTEST_WINDOW["start"])
    end = date.fromisoformat(BACKTEST_WINDOW["end"])
    events: list[dict[str, Any]] = []
    for i in range(1, len(frame)):
        prev = frame.iloc[i - 1]
        row = frame.iloc[i]
        bullish = prev["macd"] <= prev["signal"] and row["macd"] > row["signal"]
        bearish = prev["macd"] >= prev["signal"] and row["macd"] < row["signal"]
        if not (bullish or bearish):
            continue
        bar_start = pd.Timestamp(row["timestamp"]).to_pydatetime()
        day = bar_start.date()
        if not (start <= day <= end):
            continue
        if day.isoformat() not in complete_days:
            continue
        events.append(
            {
                "date": day.isoformat(),
                "direction": "BULLISH" if bullish else "BEARISH",
                "event_timestamp": (bar_start + timedelta(minutes=5)).isoformat(),
            }
        )
    return events


def _trade_intents_for_variant(
    qualified_entries: list[dict[str, Any]],
    raw_crossovers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    force_exit = time.fromisoformat(BACKTEST_WINDOW["force_exit_time"])
    crosses_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in raw_crossovers:
        crosses_by_day[str(event["date"])].append(event)

    intents: list[dict[str, Any]] = []
    next_flat_at: dict[str, datetime] = {}

    for entry in sorted(qualified_entries, key=lambda x: x["entry_timestamp"]):
        day = str(entry["date"])
        entry_dt = datetime.fromisoformat(str(entry["entry_timestamp"]))
        if entry_dt.time() >= force_exit:
            continue
        if day in next_flat_at and entry_dt < next_flat_at[day]:
            continue

        opposite = "BEARISH" if entry["direction"] == "BULLISH" else "BULLISH"
        force_dt = datetime.combine(
            entry_dt.date(), force_exit, tzinfo=entry_dt.tzinfo
        )
        exit_dt = force_dt
        exit_reason = "FORCE_EXIT_15_20"
        for cross in sorted(
            crosses_by_day.get(day, []),
            key=lambda x: x["event_timestamp"],
        ):
            cross_dt = datetime.fromisoformat(str(cross["event_timestamp"]))
            if cross_dt <= entry_dt:
                continue
            if cross_dt > force_dt:
                break
            if cross["direction"] == opposite:
                exit_dt = cross_dt
                exit_reason = "OPPOSITE_RAW_MACD_CROSSOVER"
                break

        intents.append(
            {
                **entry,
                "exit_timestamp": exit_dt.isoformat(),
                "exit_reason": exit_reason,
            }
        )
        next_flat_at[day] = exit_dt
    return intents


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


def _costs(
    entry_open: float,
    exit_open: float,
    slippage: float,
) -> dict[str, float]:
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
    scored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    sensitivity = [
        float(x) for x in COST_MODEL["slippage_sensitivity_points_each_side"]
    ]
    primary = float(COST_MODEL["primary_slippage_points_each_side"])

    for n, intent in enumerate(intents, start=1):
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
                {"trade_number": n, **intent, "skip_reasons": missing}
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
        scored.append(
            {
                "trade_number": n,
                **intent,
                "entry_option_open": entry_open,
                "exit_option_open": exit_open,
                "premium_return_pct_gross_before_costs": round(
                    (exit_open - entry_open) / entry_open * 100.0, 4
                ),
                "hold_minutes": int(
                    (exit_dt - entry_dt).total_seconds() // 60
                ),
                "primary_cost_model": _costs(
                    entry_open, exit_open, primary
                ),
                "slippage_sensitivity": {
                    f"{slip:.2f}": _costs(entry_open, exit_open, slip)
                    for slip in sensitivity
                },
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
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "total_net_pnl_inr": 0.0,
            "profit_factor": None,
        }
    net = [float(t["primary_cost_model"]["net_pnl_inr"]) for t in trades]
    gross = [float(t["primary_cost_model"]["gross_pnl_inr"]) for t in trades]
    pf = _profit_factor(net)
    wins = sum(v > 0 for v in net)
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": sum(v < 0 for v in net),
        "win_rate_pct": round(wins / len(net) * 100.0, 2),
        "total_gross_pnl_inr": round(sum(gross), 2),
        "total_net_pnl_inr": round(sum(net), 2),
        "average_net_pnl_inr": round(sum(net) / len(net), 2),
        "median_net_pnl_inr": round(float(pd.Series(net).median()), 2),
        "profit_factor": (
            None if pf is None else ("INF" if math.isinf(pf) else round(pf, 4))
        ),
        "max_drawdown_inr": round(_max_drawdown(net), 2),
        "largest_win_inr": round(max(net), 2),
        "largest_loss_inr": round(min(net), 2),
        "average_hold_minutes": round(
            sum(int(t["hold_minutes"]) for t in trades) / len(trades), 2
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


def backtest(
    payload: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("F2 market artifact protocol mismatch")
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("F2 market artifact must be outcome-unscored")

    spot_rows = list(payload.get("spot_rows") or [])
    option_rows = list(payload.get("option_rows") or [])
    complete = _complete_days(spot_rows)
    raw_crosses = _raw_crossovers(spot_rows, complete)

    variants: dict[str, Any] = {}
    for name, spec in VARIANTS.items():
        qualified = [
            event
            for event in _qualified_entries(
                spot_rows, int(spec["confirmation_bars"])
            )
            if str(event["date"]) in complete
        ]
        intents = _trade_intents_for_variant(qualified, raw_crosses)
        trades, skipped = _score(intents, option_rows)
        calls = [t for t in trades if t["right"] == "CE"]
        puts = [t for t in trades if t["right"] == "PE"]
        variants[name] = {
            "confirmation_bars": int(spec["confirmation_bars"]),
            "quality": {
                "qualified_entries": len(qualified),
                "intended_trades": len(intents),
                "scorable_trades": len(trades),
                "skipped_trades": len(skipped),
                "trade_price_coverage_pct": (
                    round(len(trades) / len(intents) * 100.0, 2)
                    if intents
                    else None
                ),
            },
            "summary": _summary(trades),
            "call_summary": _summary(calls),
            "put_summary": _summary(puts),
            "slippage_sensitivity": _slippage_summary(trades),
            "trades": trades,
            "skipped_trade_intents": skipped,
        }

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "backtest_window": BACKTEST_WINDOW,
        "complete_spot_sessions": len(complete),
        "raw_macd_crossovers": len(raw_crosses),
        "variants": variants,
        "comparison": {
            name: {
                "confirmation_bars": result["confirmation_bars"],
                "trades": result["summary"]["trades"],
                "win_rate_pct": result["summary"]["win_rate_pct"],
                "total_net_pnl_inr": result["summary"]["total_net_pnl_inr"],
                "profit_factor": result["summary"]["profit_factor"],
                "max_drawdown_inr": result["summary"].get("max_drawdown_inr"),
            }
            for name, result in variants.items()
        },
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest Strategy F2 histogram persistence variants"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f2_macd_histogram_backtest_2026_08.json"),
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
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "source_market_sha256": source_sha,
                "complete_spot_sessions": report["complete_spot_sessions"],
                "raw_macd_crossovers": report["raw_macd_crossovers"],
                "comparison": report["comparison"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
