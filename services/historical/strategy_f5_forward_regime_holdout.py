"""Evaluate the frozen F5 forward entry-time regime holdout."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
    _bar_open_lookup,
    _decorate,
    _observations,
    _one_min_open_lookup,
    _trail_trade_simulation,
)
from services.historical.strategy_f5_forward_regime_holdout_protocol import (
    ENTRY_TIME_REGIME,
    GUARDRAILS,
    PRIMARY_HYPOTHESIS,
    PROTOCOL_VERSION,
    ROLE,
    SECONDARY_REPORTING,
    STRATEGY_ID,
    VALIDATION_GATE,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_FORWARD_REGIME_HOLDOUT_BACKTEST_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vote(value: float, *, epsilon: float = 1e-12) -> str:
    if value > epsilon:
        return "BULLISH"
    if value < -epsilon:
        return "BEARISH"
    return "FLAT"


def _entry_time_regime(
    rows: list[dict[str, Any]],
    entry_timestamp: str,
) -> dict[str, Any]:
    entry_dt = datetime.fromisoformat(entry_timestamp)
    day_rows = sorted(
        [
            row
            for row in rows
            if date.fromisoformat(str(row["date"])) == entry_dt.date()
        ],
        key=lambda r: str(r["timestamp"]),
    )
    opening = next(
        (
            row
            for row in day_rows
            if datetime.fromisoformat(str(row["timestamp"])).strftime("%H:%M")
            == str(WINDOW["session_start"])
        ),
        None,
    )
    if opening is None:
        return {"available": False, "reason": "MISSING_0915_OPEN", "regime": None}

    interval = timedelta(minutes=int(WINDOW["spot_interval_minutes"]))
    completed = [
        row
        for row in day_rows
        if datetime.fromisoformat(str(row["timestamp"])) + interval <= entry_dt
    ]
    minimum = int(ENTRY_TIME_REGIME["minimum_completed_5m_bars"])
    if len(completed) < minimum:
        return {
            "available": False,
            "reason": "INSUFFICIENT_COMPLETED_5M_BARS",
            "regime": None,
            "completed_5m_bars": len(completed),
        }

    open_price = float(opening["open"])
    closes = np.asarray([float(row["close"]) for row in completed], dtype=float)
    last_close = float(closes[-1])
    median_close = float(np.median(closes))
    x = np.arange(len(closes), dtype=float)
    slope_points = float(np.polyfit(x, closes, 1)[0])

    net_location_pct = 100.0 * (last_close / open_price - 1.0)
    median_location_pct = 100.0 * (median_close / open_price - 1.0)
    slope_pct_per_bar = 100.0 * slope_points / open_price

    votes = {
        "net_return_vote": _vote(net_location_pct),
        "median_location_vote": _vote(median_location_pct),
        "session_slope_vote": _vote(slope_pct_per_bar),
    }
    if all(v == "BULLISH" for v in votes.values()):
        regime = "BULLISH"
    elif all(v == "BEARISH" for v in votes.values()):
        regime = "BEARISH"
    else:
        regime = "MIXED"

    return {
        "available": True,
        "regime": regime,
        "entry_timestamp": entry_timestamp,
        "spot_0915_open": round(open_price, 4),
        "last_completed_5m_close": round(last_close, 4),
        "net_location_pct_vs_open": round(net_location_pct, 4),
        "median_completed_5m_close": round(median_close, 4),
        "median_location_pct_vs_open": round(median_location_pct, 4),
        "session_slope_points_per_5m_bar": round(slope_points, 6),
        "session_slope_pct_per_5m_bar": round(slope_pct_per_bar, 6),
        "completed_5m_bars": len(completed),
        "last_completed_bar_start": str(completed[-1]["timestamp"]),
        "votes": votes,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "trail_activations": 0,
            "trail_activation_rate_pct": None,
            "net_pnl_inr": 0.0,
            "average_net_pnl_inr": None,
            "median_net_pnl_inr": None,
        }
    pnl = [float(r["net_pnl_inr"]) for r in rows]
    ordered = sorted(pnl)
    n = len(ordered)
    median = (
        ordered[n // 2]
        if n % 2
        else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0
    )
    wins = sum(v > 0 for v in pnl)
    activations = sum(bool(r["trail_activated"]) for r in rows)
    return {
        "trades": n,
        "wins": wins,
        "losses": sum(v < 0 for v in pnl),
        "win_rate_pct": round(wins / n * 100.0, 2),
        "trail_activations": activations,
        "trail_activation_rate_pct": round(activations / n * 100.0, 2),
        "net_pnl_inr": round(sum(pnl), 2),
        "average_net_pnl_inr": round(sum(pnl) / n, 2),
        "median_net_pnl_inr": round(median, 2),
    }


def _gate(
    *,
    target_sessions: int,
    bearish_pe: list[dict[str, Any]],
    bearish_ce: list[dict[str, Any]],
    daily: dict[str, Any],
) -> dict[str, Any]:
    pe = _summary(bearish_pe)
    ce = _summary(bearish_ce)
    bearish_days = [
        item
        for item in daily.values()
        if item["bearish_entry_context_trades"] > 0
    ]
    pe_above_ce_days = sum(
        float(item["bearish_PE"]["net_pnl_inr"])
        > float(item["bearish_CE"]["net_pnl_inr"])
        for item in bearish_days
    )
    pct_days = (
        round(pe_above_ce_days / len(bearish_days) * 100.0, 2)
        if bearish_days
        else None
    )

    coverage_checks = {
        "minimum_target_sessions": (
            target_sessions >= int(VALIDATION_GATE["minimum_target_sessions"])
        ),
        "minimum_bearish_PE_trades": (
            int(pe["trades"]) >= int(VALIDATION_GATE["minimum_bearish_PE_trades"])
        ),
        "minimum_bearish_CE_trades": (
            int(ce["trades"]) >= int(VALIDATION_GATE["minimum_bearish_CE_trades"])
        ),
    }
    if not all(coverage_checks.values()):
        status = "INCONCLUSIVE_COVERAGE"
    else:
        effect_checks = {
            "bearish_PE_net_positive": float(pe["net_pnl_inr"]) > 0.0,
            "bearish_CE_net_negative": float(ce["net_pnl_inr"]) < 0.0,
            "bearish_PE_average_pnl_above_CE": (
                float(pe["average_net_pnl_inr"]) > float(ce["average_net_pnl_inr"])
            ),
            "bearish_PE_activation_rate_above_CE": (
                float(pe["trail_activation_rate_pct"])
                > float(ce["trail_activation_rate_pct"])
            ),
            "bearish_days_PE_net_above_CE": (
                pct_days is not None
                and pct_days
                >= float(
                    VALIDATION_GATE["minimum_pct_bearish_days_PE_net_above_CE"]
                )
            ),
        }
        status = (
            "PASS_FRESH_HOLDOUT"
            if all(effect_checks.values())
            else "FAIL_FRESH_HOLDOUT"
        )
        return {
            "status": status,
            "coverage_checks": coverage_checks,
            "effect_checks": effect_checks,
            "bearish_days_with_entry_context": len(bearish_days),
            "bearish_days_PE_net_above_CE": pe_above_ce_days,
            "pct_bearish_days_PE_net_above_CE": pct_days,
        }

    return {
        "status": status,
        "coverage_checks": coverage_checks,
        "effect_checks": None,
        "bearish_days_with_entry_context": len(bearish_days),
        "bearish_days_PE_net_above_CE": pe_above_ce_days,
        "pct_bearish_days_PE_net_above_CE": pct_days,
    }


def analyze(
    market: dict[str, Any],
    *,
    source_market_sha256: str | None = None,
) -> dict[str, Any]:
    if market.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("forward holdout market protocol mismatch")
    if market.get("strategy_outcomes_scored") is not False:
        raise ValueError("holdout market must be outcome-unscored")

    contracts = list(market.get("daily_contracts") or [])
    spot_rows = list(market.get("spot_rows") or [])
    option_rows = list(market.get("option_rows_1m") or [])

    bars_2m, incomplete = _aggregate_2m(option_rows)
    obs, insufficient = _observations(contracts, bars_2m)
    open_2m = _bar_open_lookup(bars_2m)
    open_1m = _one_min_open_lookup(option_rows)
    trades, skips = _trail_trade_simulation(contracts, obs, open_2m, open_1m)
    trades = _decorate(trades)

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    target_sessions = sorted(
        {
            str(contract["date"])
            for contract in contracts
            if start <= date.fromisoformat(str(contract["date"])) <= end
        }
    )
    spot_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in spot_rows:
        day = str(row["date"])
        if day in target_sessions:
            spot_by_day[day].append(row)

    enriched: list[dict[str, Any]] = []
    for trade in trades:
        day = str(trade["date"])
        if day not in target_sessions:
            continue
        context = _entry_time_regime(
            spot_by_day.get(day, []),
            str(trade["entry_timestamp"]),
        )
        pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
        enriched.append({
            "date": day,
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "right": str(trade["right"]),
            "trail_activated": bool(trade.get("trail_activated")),
            "net_pnl_inr": pnl,
            "winner": pnl > 0.0,
            "entry_time_regime_available": bool(context.get("available")),
            "entry_time_regime": context.get("regime"),
            "entry_time_regime_context": context,
        })

    available = [r for r in enriched if r["entry_time_regime_available"]]
    bearish = [r for r in available if r["entry_time_regime"] == "BEARISH"]
    bullish = [r for r in available if r["entry_time_regime"] == "BULLISH"]
    mixed = [r for r in available if r["entry_time_regime"] == "MIXED"]
    bearish_pe = [r for r in bearish if r["right"] == "PE"]
    bearish_ce = [r for r in bearish if r["right"] == "CE"]

    daily: dict[str, Any] = {}
    for day in target_sessions:
        day_rows = [r for r in available if r["date"] == day]
        day_bearish = [r for r in day_rows if r["entry_time_regime"] == "BEARISH"]
        daily[day] = {
            "all_available_context": _summary(day_rows),
            "bearish_entry_context_trades": len(day_bearish),
            "bearish_PE": _summary([r for r in day_bearish if r["right"] == "PE"]),
            "bearish_CE": _summary([r for r in day_bearish if r["right"] == "CE"]),
        }

    by_month = {}
    for month in WINDOW["months"]:
        rows = [r for r in available if r["month"] == month]
        b = [r for r in rows if r["entry_time_regime"] == "BEARISH"]
        by_month[month] = {
            "all": _summary(rows),
            "bearish_PE": _summary([r for r in b if r["right"] == "PE"]),
            "bearish_CE": _summary([r for r in b if r["right"] == "CE"]),
        }

    validation = _gate(
        target_sessions=len(target_sessions),
        bearish_pe=bearish_pe,
        bearish_ce=bearish_ce,
        daily=daily,
    )

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": source_market_sha256,
        "primary_hypothesis": PRIMARY_HYPOTHESIS,
        "entry_time_regime_definition": ENTRY_TIME_REGIME,
        "validation_gate_definition": VALIDATION_GATE,
        "secondary_reporting": SECONDARY_REPORTING,
        "quality": {
            "target_sessions": len(target_sessions),
            "baseline_trades": len(enriched),
            "trades_with_entry_time_regime": len(available),
            "trades_without_entry_time_regime": len(enriched) - len(available),
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
            "skipped_trade_intents": len(skips),
        },
        "primary_bearish_analysis": {
            "PE": _summary(bearish_pe),
            "CE": _summary(bearish_ce),
        },
        "secondary": {
            "bullish_CE": _summary([r for r in bullish if r["right"] == "CE"]),
            "bullish_PE": _summary([r for r in bullish if r["right"] == "PE"]),
            "mixed_CE": _summary([r for r in mixed if r["right"] == "CE"]),
            "mixed_PE": _summary([r for r in mixed if r["right"] == "PE"]),
        },
        "by_month": by_month,
        "daily": daily,
        "validation": validation,
        "trades": enriched,
        "decision": validation["status"],
        "guardrails": GUARDRAILS,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen F5 Oct-Dec forward regime holdout"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f5_forward_regime_holdout_2026_q4.json"),
    )
    args = parser.parse_args()

    source_sha = _sha256(args.market)
    report = analyze(
        json.loads(args.market.read_text(encoding="utf-8")),
        source_market_sha256=source_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "sha256": _sha256(args.output),
        "quality": report["quality"],
        "primary_bearish_analysis": report["primary_bearish_analysis"],
        "by_month": report["by_month"],
        "validation": report["validation"],
        "decision": report["decision"],
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
