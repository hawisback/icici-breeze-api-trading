"""Evaluate F5 trades against the dominant whole-day NIFTY regime."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
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
from services.historical.strategy_f5_dominant_nifty_regime_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
    REGIME,
    ROLE,
    SIDE_ALIGNMENT,
    STRATEGY_ID,
    TARGET_DATES,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_DOMINANT_NIFTY_REGIME_BACKTEST_V1"


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


def _row_at(
    rows: list[dict[str, Any]],
    hhmm: str,
) -> dict[str, Any] | None:
    for row in rows:
        ts = datetime.fromisoformat(str(row["timestamp"]))
        if ts.strftime("%H:%M") == hhmm:
            return row
    return None


def _classify_day(
    day: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda r: str(r["timestamp"]))
    opening = _row_at(rows, str(WINDOW["session_start"]))
    ending = _row_at(rows, str(WINDOW["regime_end"]))
    if opening is None or ending is None:
        return {
            "date": day,
            "available": False,
            "regime": None,
            "reason": (
                "MISSING_0915_OPEN" if opening is None else "MISSING_1520_OPEN"
            ),
        }

    end_dt = datetime.fromisoformat(str(ending["timestamp"]))
    completed = []
    interval = timedelta(minutes=int(WINDOW["spot_interval_minutes"]))
    for row in rows:
        start = datetime.fromisoformat(str(row["timestamp"]))
        if start + interval <= end_dt:
            completed.append(row)

    if len(completed) < 2:
        return {
            "date": day,
            "available": False,
            "regime": None,
            "reason": "INSUFFICIENT_COMPLETED_5M_BARS",
        }

    open_price = float(opening["open"])
    end_price = float(ending["open"])
    closes = np.asarray([float(r["close"]) for r in completed], dtype=float)
    net_return_pct = 100.0 * (end_price / open_price - 1.0)
    median_close = float(np.median(closes))
    median_location_pct = 100.0 * (median_close / open_price - 1.0)
    x = np.arange(len(closes), dtype=float)
    slope_points_per_bar = float(np.polyfit(x, closes, 1)[0])
    slope_pct_per_bar = 100.0 * slope_points_per_bar / open_price

    votes = {
        "net_return_vote": _vote(net_return_pct),
        "median_location_vote": _vote(median_location_pct),
        "session_slope_vote": _vote(slope_pct_per_bar),
    }
    if all(v == "BULLISH" for v in votes.values()):
        regime = "BULLISH"
    elif all(v == "BEARISH" for v in votes.values()):
        regime = "BEARISH"
    else:
        regime = "MIXED"

    above = int(np.sum(closes > open_price))
    below = int(np.sum(closes < open_price))
    equal = int(len(closes) - above - below)

    return {
        "date": day,
        "available": True,
        "regime": regime,
        "spot_0915_open": round(open_price, 4),
        "spot_1520_open": round(end_price, 4),
        "net_return_pct": round(net_return_pct, 4),
        "median_completed_5m_close": round(median_close, 4),
        "median_location_pct_vs_open": round(median_location_pct, 4),
        "session_slope_points_per_5m_bar": round(slope_points_per_bar, 6),
        "session_slope_pct_per_5m_bar": round(slope_pct_per_bar, 6),
        "completed_5m_bars_used": len(completed),
        "closes_above_0915_open": above,
        "closes_below_0915_open": below,
        "closes_equal_0915_open": equal,
        "pct_closes_above_open": round(above / len(closes) * 100.0, 2),
        "pct_closes_below_open": round(below / len(closes) * 100.0, 2),
        "votes": votes,
        "session_high_to_1520": round(
            max(float(r["high"]) for r in completed), 4
        ),
        "session_low_to_1520": round(
            min(float(r["low"]) for r in completed), 4
        ),
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
    wins = sum(v > 0.0 for v in pnl)
    activations = sum(bool(r["trail_activated"]) for r in rows)
    return {
        "trades": n,
        "wins": wins,
        "losses": sum(v < 0.0 for v in pnl),
        "win_rate_pct": round(wins / n * 100.0, 2),
        "trail_activations": activations,
        "trail_activation_rate_pct": round(activations / n * 100.0, 2),
        "net_pnl_inr": round(sum(pnl), 2),
        "average_net_pnl_inr": round(sum(pnl) / n, 2),
        "median_net_pnl_inr": round(median, 2),
    }


def _status(right: str, regime: str | None) -> str:
    if regime == "MIXED" or regime is None:
        return "MIXED_REGIME"
    return (
        "REGIME_ALIGNED"
        if SIDE_ALIGNMENT.get(regime) == right
        else "COUNTER_REGIME"
    )


def analyze(
    payload: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("dominant-regime market protocol mismatch")
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("market artifact must be outcome-unscored")

    contracts = list(payload.get("daily_contracts") or [])
    spot_rows = list(payload.get("spot_rows") or [])
    option_rows = list(payload.get("option_rows_1m") or [])

    bars_2m, incomplete = _aggregate_2m(option_rows)
    obs, insufficient = _observations(contracts, bars_2m)
    open_2m = _bar_open_lookup(bars_2m)
    open_1m = _one_min_open_lookup(option_rows)
    trades, skips = _trail_trade_simulation(
        contracts, obs, open_2m, open_1m
    )
    trades = _decorate(trades)

    spot_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in spot_rows:
        day = str(row["date"])
        if day in TARGET_DATES:
            spot_by_day[day].append(row)

    regimes = {
        day: _classify_day(day, spot_by_day.get(day, []))
        for day in TARGET_DATES
    }

    enriched: list[dict[str, Any]] = []
    for trade in trades:
        day = str(trade["date"])
        if day not in TARGET_DATES:
            continue
        regime = regimes.get(day, {}).get("regime")
        pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
        enriched.append({
            "date": day,
            "entry_timestamp": str(trade["entry_timestamp"]),
            "right": str(trade["right"]),
            "expiry": str(trade["expiry"]),
            "strike": int(trade["strike"]),
            "entry_open": float(trade["entry_open"]),
            "exit_timestamp": str(trade["exit_timestamp"]),
            "exit_reason": str(trade["exit_reason"]),
            "trail_activated": bool(trade.get("trail_activated")),
            "net_pnl_inr": pnl,
            "winner": pnl > 0.0,
            "dominant_nifty_regime": regime,
            "regime_status": _status(str(trade["right"]), regime),
        })

    daily: dict[str, Any] = {}
    for day in TARGET_DATES:
        day_rows = [r for r in enriched if r["date"] == day]
        daily[day] = {
            "nifty_regime": regimes[day],
            "all_trades": _summary(day_rows),
            "CE": _summary([r for r in day_rows if r["right"] == "CE"]),
            "PE": _summary([r for r in day_rows if r["right"] == "PE"]),
            "regime_aligned": _summary([
                r for r in day_rows if r["regime_status"] == "REGIME_ALIGNED"
            ]),
            "counter_regime": _summary([
                r for r in day_rows if r["regime_status"] == "COUNTER_REGIME"
            ]),
            "mixed_regime": _summary([
                r for r in day_rows if r["regime_status"] == "MIXED_REGIME"
            ]),
        }

    aligned = [
        r for r in enriched if r["regime_status"] == "REGIME_ALIGNED"
    ]
    counter = [
        r for r in enriched if r["regime_status"] == "COUNTER_REGIME"
    ]
    mixed = [
        r for r in enriched if r["regime_status"] == "MIXED_REGIME"
    ]

    by_regime_and_side = {
        regime: {
            side: _summary([
                r
                for r in enriched
                if r["dominant_nifty_regime"] == regime
                and r["right"] == side
            ])
            for side in ("CE", "PE")
        }
        for regime in ("BULLISH", "BEARISH", "MIXED")
    }

    oct1 = [r for r in enriched if r["date"] == "2026-10-01"]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "regime_definition": REGIME,
        "target_dates": TARGET_DATES,
        "quality": {
            "target_sessions_expected": len(TARGET_DATES),
            "target_contract_sessions": len(contracts),
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
            "baseline_trades": len(enriched),
            "skipped_trade_intents": len(skips),
            "regime_available_days": sum(
                bool(regimes[d].get("available")) for d in TARGET_DATES
            ),
        },
        "daily": daily,
        "matched_trade_regime_comparison": {
            "regime_aligned": _summary(aligned),
            "counter_regime": _summary(counter),
            "mixed_regime": _summary(mixed),
        },
        "by_regime_and_side": by_regime_and_side,
        "october_1": {
            "nifty_regime": regimes["2026-10-01"],
            "all_trades": _summary(oct1),
            "CE": _summary([r for r in oct1 if r["right"] == "CE"]),
            "PE": _summary([r for r in oct1 if r["right"] == "PE"]),
            "regime_aligned": _summary([
                r for r in oct1 if r["regime_status"] == "REGIME_ALIGNED"
            ]),
            "counter_regime": _summary([
                r for r in oct1 if r["regime_status"] == "COUNTER_REGIME"
            ]),
            "trades": oct1,
        },
        "trades": enriched,
        "decision": "DOMINANT_NIFTY_REGIME_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
        "guardrails": GUARDRAILS,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate F5 against dominant whole-day NIFTY regime"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_dominant_nifty_regime_2026_09_21_10_01.json"
        ),
    )
    args = parser.parse_args()

    source_sha = _sha256(args.market)
    payload = json.loads(args.market.read_text(encoding="utf-8"))
    report = analyze(payload, source_sha256=source_sha)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = _sha256(args.output)

    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_market_sha256": source_sha,
        "quality": report["quality"],
        "daily": report["daily"],
        "matched_trade_regime_comparison": report[
            "matched_trade_regime_comparison"
        ],
        "by_regime_and_side": report["by_regime_and_side"],
        "october_1": report["october_1"],
        "decision": report["decision"],
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
