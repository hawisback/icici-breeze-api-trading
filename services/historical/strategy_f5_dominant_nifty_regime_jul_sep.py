"""Expand the frozen dominant-NIFTY-regime diagnostic across Jul-Sep 2026."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_dominant_nifty_regime import _classify_day
from services.historical.strategy_f5_dominant_nifty_regime_jul_sep_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
    REGIME_DEFINITION,
    ROLE,
    SIDE_ALIGNMENT,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_BACKTEST_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    if regime in {None, "MIXED"}:
        return "MIXED_REGIME"
    return (
        "REGIME_ALIGNED"
        if SIDE_ALIGNMENT.get(regime) == right
        else "COUNTER_REGIME"
    )


def _daily_comparison(
    day: str,
    regime: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    ce = [r for r in rows if r["right"] == "CE"]
    pe = [r for r in rows if r["right"] == "PE"]
    aligned = [r for r in rows if r["regime_status"] == "REGIME_ALIGNED"]
    counter = [r for r in rows if r["regime_status"] == "COUNTER_REGIME"]
    mixed = [r for r in rows if r["regime_status"] == "MIXED_REGIME"]
    ce_s = _summary(ce)
    pe_s = _summary(pe)

    if regime.get("regime") == "BEARISH":
        preferred = pe_s
        counter_side = ce_s
        preferred_side = "PE"
        counter_name = "CE"
    elif regime.get("regime") == "BULLISH":
        preferred = ce_s
        counter_side = pe_s
        preferred_side = "CE"
        counter_name = "PE"
    else:
        preferred = _summary([])
        counter_side = _summary([])
        preferred_side = None
        counter_name = None

    return {
        "date": day,
        "nifty_regime": regime,
        "all_trades": _summary(rows),
        "CE": ce_s,
        "PE": pe_s,
        "regime_aligned": _summary(aligned),
        "counter_regime": _summary(counter),
        "mixed_regime": _summary(mixed),
        "preferred_side": preferred_side,
        "counter_side": counter_name,
        "preferred_side_net_minus_counter_inr": (
            None
            if preferred_side is None
            else round(
                float(preferred["net_pnl_inr"])
                - float(counter_side["net_pnl_inr"]),
                2,
            )
        ),
    }


def _month_report(
    month: str,
    enriched: list[dict[str, Any]],
    regimes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = [r for r in enriched if r["month"] == month]
    days = sorted(
        day
        for day, regime in regimes.items()
        if day.startswith(month) and regime.get("available")
    )
    regime_counts = {
        name: sum(regimes[d].get("regime") == name for d in days)
        for name in ("BULLISH", "BEARISH", "MIXED")
    }
    return {
        "days": len(days),
        "regime_day_counts": regime_counts,
        "all_trades": _summary(rows),
        "regime_aligned": _summary([
            r for r in rows if r["regime_status"] == "REGIME_ALIGNED"
        ]),
        "counter_regime": _summary([
            r for r in rows if r["regime_status"] == "COUNTER_REGIME"
        ]),
        "mixed_regime": _summary([
            r for r in rows if r["regime_status"] == "MIXED_REGIME"
        ]),
        "by_regime_and_side": {
            regime: {
                side: _summary([
                    r
                    for r in rows
                    if r["dominant_nifty_regime"] == regime
                    and r["right"] == side
                ])
                for side in ("CE", "PE")
            }
            for regime in ("BULLISH", "BEARISH", "MIXED")
        },
    }


def analyze(
    market: dict[str, Any],
    backtest: dict[str, Any],
    *,
    market_sha256: str,
    backtest_sha256: str,
) -> dict[str, Any]:
    if market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != market_sha256:
        raise ValueError("F5 backtest is not bound to supplied market artifact")

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])

    spot_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in list(market.get("spot_rows") or []):
        day = date.fromisoformat(str(row["date"]))
        if start <= day <= end:
            spot_by_day[day.isoformat()].append(row)

    regimes = {
        day: _classify_day(day, rows)
        for day, rows in sorted(spot_by_day.items())
    }

    baseline = list(
        backtest["candidates"][BASELINE_CANDIDATE].get("trades") or []
    )
    selected = [
        t
        for t in baseline
        if start <= date.fromisoformat(str(t["date"])) <= end
    ]

    enriched: list[dict[str, Any]] = []
    missing_regime_trades = 0
    for trade in selected:
        day = str(trade["date"])
        regime = regimes.get(day)
        regime_name = None if regime is None else regime.get("regime")
        if regime_name is None:
            missing_regime_trades += 1
        pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
        enriched.append({
            "date": day,
            "month": str(trade["month"]),
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
            "dominant_nifty_regime": regime_name,
            "regime_status": _status(str(trade["right"]), regime_name),
        })

    daily = {
        day: _daily_comparison(
            day,
            regimes[day],
            [r for r in enriched if r["date"] == day],
        )
        for day in sorted(regimes)
    }

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

    bearish_days = [
        d for d in daily.values()
        if d["nifty_regime"].get("regime") == "BEARISH"
    ]
    bullish_days = [
        d for d in daily.values()
        if d["nifty_regime"].get("regime") == "BULLISH"
    ]

    bearish_day_checks = {
        "bearish_days": len(bearish_days),
        "days_pe_net_above_ce": sum(
            float(d["PE"]["net_pnl_inr"]) > float(d["CE"]["net_pnl_inr"])
            for d in bearish_days
        ),
        "days_ce_zero_trail_activations": sum(
            int(d["CE"]["trail_activations"]) == 0 for d in bearish_days
        ),
        "days_pe_activation_rate_above_ce": sum(
            (
                d["PE"]["trail_activation_rate_pct"] is not None
                and d["CE"]["trail_activation_rate_pct"] is not None
                and float(d["PE"]["trail_activation_rate_pct"])
                > float(d["CE"]["trail_activation_rate_pct"])
            )
            for d in bearish_days
        ),
    }
    bullish_day_checks = {
        "bullish_days": len(bullish_days),
        "days_ce_net_above_pe": sum(
            float(d["CE"]["net_pnl_inr"]) > float(d["PE"]["net_pnl_inr"])
            for d in bullish_days
        ),
        "days_pe_zero_trail_activations": sum(
            int(d["PE"]["trail_activations"]) == 0 for d in bullish_days
        ),
        "days_ce_activation_rate_above_pe": sum(
            (
                d["CE"]["trail_activation_rate_pct"] is not None
                and d["PE"]["trail_activation_rate_pct"] is not None
                and float(d["CE"]["trail_activation_rate_pct"])
                > float(d["PE"]["trail_activation_rate_pct"])
            )
            for d in bullish_days
        ),
    }

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "baseline_candidate": BASELINE_CANDIDATE,
        "window": WINDOW,
        "regime_definition": REGIME_DEFINITION,
        "quality": {
            "spot_sessions": len(regimes),
            "regime_available_sessions": sum(
                bool(r.get("available")) for r in regimes.values()
            ),
            "baseline_trades": len(selected),
            "matched_trades": len(enriched),
            "trades_missing_regime": missing_regime_trades,
        },
        "overall": {
            "all_trades": _summary(enriched),
            "regime_aligned": _summary([
                r for r in enriched if r["regime_status"] == "REGIME_ALIGNED"
            ]),
            "counter_regime": _summary([
                r for r in enriched if r["regime_status"] == "COUNTER_REGIME"
            ]),
            "mixed_regime": _summary([
                r for r in enriched if r["regime_status"] == "MIXED_REGIME"
            ]),
        },
        "by_regime_and_side": by_regime_and_side,
        "bearish_day_checks": bearish_day_checks,
        "bullish_day_checks": bullish_day_checks,
        "by_month": {
            month: _month_report(month, enriched, regimes)
            for month in WINDOW["months"]
        },
        "daily": daily,
        "trades": enriched,
        "decision": (
            "JUL_SEP_DOMINANT_NIFTY_REGIME_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED"
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expand dominant NIFTY regime diagnostic across Jul-Sep"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_dominant_nifty_regime_jul_sep_2026.json"
        ),
    )
    args = parser.parse_args()

    market_sha = _sha256(args.market)
    backtest_sha = _sha256(args.backtest)
    market = json.loads(args.market.read_text(encoding="utf-8"))
    backtest = json.loads(args.backtest.read_text(encoding="utf-8"))
    report = analyze(
        market,
        backtest,
        market_sha256=market_sha,
        backtest_sha256=backtest_sha,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = _sha256(args.output)

    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "quality": report["quality"],
        "overall": report["overall"],
        "by_regime_and_side": report["by_regime_and_side"],
        "bearish_day_checks": report["bearish_day_checks"],
        "bullish_day_checks": report["bullish_day_checks"],
        "by_month": report["by_month"],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
