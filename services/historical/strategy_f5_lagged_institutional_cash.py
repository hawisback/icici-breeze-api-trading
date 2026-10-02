"""Analyze prior-session institutional cash flow against F5 outcomes."""
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
from services.historical.strategy_f5_lagged_institutional_cash_protocol import (
    FLOW_STATE,
    GUARDRAILS,
    OFFSET_STATE,
    PROTOCOL_VERSION,
    ROLE,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_BACKTEST_V1"
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


def _flow_state(value: float) -> str:
    if value > 0:
        return "BUYING"
    if value < 0:
        return "SELLING"
    return "FLAT"


def _offset_state(fii_state: str, dii_state: str) -> str:
    pair = (fii_state, dii_state)
    return {
        ("SELLING", "BUYING"): "FII_SELL_DII_BUY",
        ("BUYING", "SELLING"): "FII_BUY_DII_SELL",
        ("BUYING", "BUYING"): "BOTH_BUY",
        ("SELLING", "SELLING"): "BOTH_SELL",
    }.get(pair, "OTHER")


def _context_by_session(
    trading_sessions: list[date],
    cash_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_date = {str(row["date"]): row for row in cash_rows}
    result: dict[str, dict[str, Any]] = {}
    for i, day in enumerate(trading_sessions):
        day_key = day.isoformat()
        if i < 1:
            result[day_key] = {
                "available": False,
                "reason": "NO_PRIOR_TRADING_SESSION",
            }
            continue
        prior = trading_sessions[i - 1]
        raw = by_date.get(prior.isoformat())
        if raw is None:
            result[day_key] = {
                "available": False,
                "reason": "PRIOR_CASH_REPORT_MISSING",
                "prior_report_date": prior.isoformat(),
            }
            continue
        required = ("FII_net_cr", "DII_net_cr")
        if any(raw.get(k) is None for k in required):
            result[day_key] = {
                "available": False,
                "reason": "PRIOR_CASH_REPORT_INCOMPLETE",
                "prior_report_date": prior.isoformat(),
            }
            continue
        fii_state = _flow_state(float(raw["FII_net_cr"]))
        dii_state = _flow_state(float(raw["DII_net_cr"]))
        result[day_key] = {
            "available": True,
            "prior_report_date": prior.isoformat(),
            "FII": {
                "buy_cr": (
                    None
                    if raw.get("FII_buy_cr") is None
                    else float(raw["FII_buy_cr"])
                ),
                "sell_cr": (
                    None
                    if raw.get("FII_sell_cr") is None
                    else float(raw["FII_sell_cr"])
                ),
                "net_cr": float(raw["FII_net_cr"]),
                "flow_state": fii_state,
            },
            "DII": {
                "buy_cr": (
                    None
                    if raw.get("DII_buy_cr") is None
                    else float(raw["DII_buy_cr"])
                ),
                "sell_cr": (
                    None
                    if raw.get("DII_sell_cr") is None
                    else float(raw["DII_sell_cr"])
                ),
                "net_cr": float(raw["DII_net_cr"]),
                "flow_state": dii_state,
            },
            "offset_state": _offset_state(fii_state, dii_state),
        }
    return result


def _month_report(month: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    group = [r for r in rows if r["month"] == month]
    bearish_pe = [
        r for r in group
        if r["dominant_nifty_regime"] == "BEARISH" and r["right"] == "PE"
    ]
    bearish_ce = [
        r for r in group
        if r["dominant_nifty_regime"] == "BEARISH" and r["right"] == "CE"
    ]
    bullish_ce = [
        r for r in group
        if r["dominant_nifty_regime"] == "BULLISH" and r["right"] == "CE"
    ]
    return {
        "all_trades_with_context": _summary(group),
        "bearish_regime": {
            "PE_all_with_context": _summary(bearish_pe),
            "PE_prior_FII_selling": _summary([
                r for r in bearish_pe if r["FII_flow_state"] == "SELLING"
            ]),
            "PE_prior_FII_not_selling": _summary([
                r for r in bearish_pe if r["FII_flow_state"] != "SELLING"
            ]),
            "PE_prior_FII_sell_DII_buy": _summary([
                r for r in bearish_pe
                if r["cash_offset_state"] == "FII_SELL_DII_BUY"
            ]),
            "CE_all_with_context": _summary(bearish_ce),
            "CE_prior_FII_selling": _summary([
                r for r in bearish_ce if r["FII_flow_state"] == "SELLING"
            ]),
        },
        "bullish_regime": {
            "CE_all_with_context": _summary(bullish_ce),
            "CE_prior_FII_buying": _summary([
                r for r in bullish_ce if r["FII_flow_state"] == "BUYING"
            ]),
            "CE_prior_FII_not_buying": _summary([
                r for r in bullish_ce if r["FII_flow_state"] != "BUYING"
            ]),
        },
    }


def analyze(
    f5_market: dict[str, Any],
    backtest: dict[str, Any],
    cash_market: dict[str, Any],
    *,
    f5_market_sha256: str,
    backtest_sha256: str,
    cash_market_sha256: str,
) -> dict[str, Any]:
    if f5_market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if f5_market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != f5_market_sha256:
        raise ValueError("F5 backtest not bound to supplied market")
    if cash_market.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("cash market protocol mismatch")
    if cash_market.get("source_f5_market_sha256") != f5_market_sha256:
        raise ValueError("cash market not bound to supplied F5 market")

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    all_sessions = sorted({
        date.fromisoformat(str(row["date"]))
        for row in list(f5_market.get("spot_rows") or [])
    })
    spot_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in list(f5_market.get("spot_rows") or []):
        d = date.fromisoformat(str(row["date"]))
        if start <= d <= end:
            spot_by_day[d.isoformat()].append(row)
    regimes = {
        day: _classify_day(day, rows)
        for day, rows in sorted(spot_by_day.items())
    }
    contexts = _context_by_session(
        all_sessions,
        list(cash_market.get("cash_rows") or []),
    )

    baseline = list(
        backtest["candidates"][BASELINE_CANDIDATE].get("trades") or []
    )
    selected = [
        t for t in baseline
        if start <= date.fromisoformat(str(t["date"])) <= end
    ]

    enriched: list[dict[str, Any]] = []
    for trade in selected:
        day = str(trade["date"])
        context = contexts.get(day, {})
        fii = context.get("FII") or {}
        dii = context.get("DII") or {}
        pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
        enriched.append({
            "date": day,
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "right": str(trade["right"]),
            "trail_activated": bool(trade.get("trail_activated")),
            "net_pnl_inr": pnl,
            "winner": pnl > 0.0,
            "dominant_nifty_regime": regimes.get(day, {}).get("regime"),
            "cash_context_available": bool(context.get("available")),
            "cash_unavailable_reason": (
                None if context.get("available") else context.get("reason")
            ),
            "prior_cash_report_date": context.get("prior_report_date"),
            "FII_net_cr": fii.get("net_cr"),
            "FII_flow_state": fii.get("flow_state"),
            "DII_net_cr": dii.get("net_cr"),
            "DII_flow_state": dii.get("flow_state"),
            "cash_offset_state": context.get("offset_state"),
        })

    available = [r for r in enriched if r["cash_context_available"]]
    unavailable = [r for r in enriched if not r["cash_context_available"]]
    bearish_pe = [
        r for r in available
        if r["dominant_nifty_regime"] == "BEARISH" and r["right"] == "PE"
    ]
    bearish_ce = [
        r for r in available
        if r["dominant_nifty_regime"] == "BEARISH" and r["right"] == "CE"
    ]
    bullish_ce = [
        r for r in available
        if r["dominant_nifty_regime"] == "BULLISH" and r["right"] == "CE"
    ]

    target_days = sorted(spot_by_day)
    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_f5_market_sha256": f5_market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "source_cash_market_sha256": cash_market_sha256,
        "flow_state_definition": FLOW_STATE,
        "offset_state_definition": OFFSET_STATE,
        "quality": {
            "target_sessions": len(target_days),
            "sessions_with_lagged_cash_context": sum(
                bool(contexts.get(day, {}).get("available"))
                for day in target_days
            ),
            "sessions_without_lagged_cash_context": sum(
                not bool(contexts.get(day, {}).get("available"))
                for day in target_days
            ),
            "baseline_trades": len(selected),
            "trades_with_lagged_cash_context": len(available),
            "trades_without_lagged_cash_context": len(unavailable),
        },
        "daily_lagged_cash_context": {
            day: contexts.get(day) for day in target_days
        },
        "primary_bearish_analysis": {
            "PE_all_with_context": _summary(bearish_pe),
            "PE_prior_FII_selling": _summary([
                r for r in bearish_pe if r["FII_flow_state"] == "SELLING"
            ]),
            "PE_prior_FII_not_selling": _summary([
                r for r in bearish_pe if r["FII_flow_state"] != "SELLING"
            ]),
            "PE_prior_FII_sell_DII_buy": _summary([
                r for r in bearish_pe
                if r["cash_offset_state"] == "FII_SELL_DII_BUY"
            ]),
            "PE_prior_not_FII_sell_DII_buy": _summary([
                r for r in bearish_pe
                if r["cash_offset_state"] != "FII_SELL_DII_BUY"
            ]),
            "CE_all_with_context": _summary(bearish_ce),
            "CE_prior_FII_selling": _summary([
                r for r in bearish_ce if r["FII_flow_state"] == "SELLING"
            ]),
            "CE_prior_FII_not_selling": _summary([
                r for r in bearish_ce if r["FII_flow_state"] != "SELLING"
            ]),
        },
        "secondary_bullish_analysis": {
            "CE_all_with_context": _summary(bullish_ce),
            "CE_prior_FII_buying": _summary([
                r for r in bullish_ce if r["FII_flow_state"] == "BUYING"
            ]),
            "CE_prior_FII_not_buying": _summary([
                r for r in bullish_ce if r["FII_flow_state"] != "BUYING"
            ]),
        },
        "by_month": {
            month: _month_report(month, available)
            for month in WINDOW["months"]
        },
        "trades": enriched,
        "decision": "LAGGED_INSTITUTIONAL_CASH_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze lagged institutional cash flow for F5"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument("--cash-market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_lagged_institutional_cash_2026_07_09.json"
        ),
    )
    args = parser.parse_args()

    f5_sha = _sha256(args.f5_market)
    backtest_sha = _sha256(args.backtest)
    cash_sha = _sha256(args.cash_market)
    report = analyze(
        json.loads(args.f5_market.read_text(encoding="utf-8")),
        json.loads(args.backtest.read_text(encoding="utf-8")),
        json.loads(args.cash_market.read_text(encoding="utf-8")),
        f5_market_sha256=f5_sha,
        backtest_sha256=backtest_sha,
        cash_market_sha256=cash_sha,
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
        "secondary_bullish_analysis": report["secondary_bullish_analysis"],
        "by_month": report["by_month"],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
