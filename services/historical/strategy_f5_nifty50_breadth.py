"""Analyze NIFTY 50 breadth against dominant-regime F5 outcomes."""
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
from services.historical.strategy_f5_nifty50_breadth_market import _membership
from services.historical.strategy_f5_nifty50_breadth_protocol import (
    BREADTH_DEFINITION,
    DATA_QUALITY,
    GUARDRAILS,
    PROTOCOL_VERSION,
    ROLE,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_NIFTY50_BREADTH_BACKTEST_V1"
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


def _breadth_day(
    day: str,
    point_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    d = date.fromisoformat(day)
    members = _membership(d)
    by_symbol: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in point_rows:
        if str(row["date"]) != day:
            continue
        by_symbol[str(row["nse_symbol"])][str(row["time"])] = row

    up = down = flat = 0
    complete = 0
    component_rows = []
    for symbol in members:
        same = by_symbol.get(symbol, {})
        opening = same.get("09:15")
        ending = same.get("15:20")
        if opening is None or ending is None:
            continue
        complete += 1
        p0 = float(opening["open"])
        p1 = float(ending["open"])
        ret = 100.0 * (p1 / p0 - 1.0)
        if ret > 0.0:
            direction = "UP"
            up += 1
        elif ret < 0.0:
            direction = "DOWN"
            down += 1
        else:
            direction = "FLAT"
            flat += 1
        component_rows.append({
            "nse_symbol": symbol,
            "open_0915": round(p0, 4),
            "open_1520": round(p1, 4),
            "return_pct": round(ret, 4),
            "direction": direction,
        })

    expected = len(members)
    coverage_pct = 100.0 * complete / expected if expected else 0.0
    quality_ok = coverage_pct >= float(
        DATA_QUALITY["minimum_constituent_coverage_pct"]
    )
    if not quality_ok:
        direction = None
    elif up > down:
        direction = "BULLISH"
    elif down > up:
        direction = "BEARISH"
    else:
        direction = "MIXED"

    directional = up + down
    return {
        "date": day,
        "available": quality_ok,
        "expected_constituents": expected,
        "complete_constituents": complete,
        "coverage_pct": round(coverage_pct, 2),
        "advancers": up,
        "decliners": down,
        "flat": flat,
        "breadth_direction": direction,
        "net_breadth_count": up - down,
        "net_breadth_pct_of_complete": (
            None
            if complete == 0
            else round((up - down) / complete * 100.0, 2)
        ),
        "advancer_pct_of_complete": (
            None if complete == 0 else round(up / complete * 100.0, 2)
        ),
        "decliner_pct_of_complete": (
            None if complete == 0 else round(down / complete * 100.0, 2)
        ),
        "directional_constituents": directional,
        "components": component_rows,
    }


def _month_report(month: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    group = [r for r in rows if r["month"] == month]
    bearish_pe = [
        r
        for r in group
        if r["dominant_nifty_regime"] == "BEARISH" and r["right"] == "PE"
    ]
    bearish_ce = [
        r
        for r in group
        if r["dominant_nifty_regime"] == "BEARISH" and r["right"] == "CE"
    ]
    bullish_ce = [
        r
        for r in group
        if r["dominant_nifty_regime"] == "BULLISH" and r["right"] == "CE"
    ]
    return {
        "all_trades": _summary(group),
        "bearish_regime": {
            "PE_all": _summary(bearish_pe),
            "PE_breadth_confirmed": _summary([
                r for r in bearish_pe if r["breadth_confirmed"]
            ]),
            "PE_breadth_available_not_confirmed": _summary([
                r for r in bearish_pe
                if r["breadth_available"] and not r["breadth_confirmed"]
            ]),
            "PE_breadth_unavailable": _summary([
                r for r in bearish_pe if not r["breadth_available"]
            ]),
            "CE_all": _summary(bearish_ce),
            "CE_breadth_confirmed": _summary([
                r for r in bearish_ce if r["breadth_confirmed"]
            ]),
            "CE_breadth_available_not_confirmed": _summary([
                r for r in bearish_ce
                if r["breadth_available"] and not r["breadth_confirmed"]
            ]),
            "CE_breadth_unavailable": _summary([
                r for r in bearish_ce if not r["breadth_available"]
            ]),
        },
        "bullish_regime": {
            "CE_all": _summary(bullish_ce),
            "CE_breadth_confirmed": _summary([
                r for r in bullish_ce if r["breadth_confirmed"]
            ]),
            "CE_breadth_available_not_confirmed": _summary([
                r for r in bullish_ce
                if r["breadth_available"] and not r["breadth_confirmed"]
            ]),
            "CE_breadth_unavailable": _summary([
                r for r in bullish_ce if not r["breadth_available"]
            ]),
        },
    }


def analyze(
    f5_market: dict[str, Any],
    backtest: dict[str, Any],
    breadth_market: dict[str, Any],
    *,
    f5_market_sha256: str,
    backtest_sha256: str,
    breadth_market_sha256: str,
) -> dict[str, Any]:
    if f5_market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if f5_market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != f5_market_sha256:
        raise ValueError("F5 backtest not bound to supplied market")
    if breadth_market.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("breadth market protocol mismatch")
    if breadth_market.get("source_f5_market_sha256") != f5_market_sha256:
        raise ValueError("breadth market not bound to supplied F5 market")

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])

    spot_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in list(f5_market.get("spot_rows") or []):
        d = date.fromisoformat(str(row["date"]))
        if start <= d <= end:
            spot_by_day[d.isoformat()].append(row)

    regimes = {
        day: _classify_day(day, rows)
        for day, rows in sorted(spot_by_day.items())
    }
    point_rows = list(breadth_market.get("constituent_points_5m") or [])
    breadth = {
        day: _breadth_day(day, point_rows)
        for day in sorted(spot_by_day)
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
    for trade in selected:
        day = str(trade["date"])
        regime = regimes.get(day, {}).get("regime")
        b = breadth.get(day, {})
        breadth_direction = b.get("breadth_direction")
        breadth_available = bool(b.get("available"))
        breadth_confirmed = (
            breadth_available
            and regime in {"BULLISH", "BEARISH"}
            and breadth_direction == regime
        )
        if not breadth_available:
            breadth_status = "UNAVAILABLE"
        elif breadth_confirmed:
            breadth_status = "CONFIRMED"
        else:
            breadth_status = "AVAILABLE_NOT_CONFIRMED"
        pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
        enriched.append({
            "date": day,
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "right": str(trade["right"]),
            "trail_activated": bool(trade.get("trail_activated")),
            "net_pnl_inr": pnl,
            "winner": pnl > 0.0,
            "dominant_nifty_regime": regime,
            "breadth_available": breadth_available,
            "breadth_direction": breadth_direction,
            "breadth_confirmed": breadth_confirmed,
            "breadth_status": breadth_status,
            "advancers": b.get("advancers"),
            "decliners": b.get("decliners"),
            "net_breadth_pct_of_complete": b.get(
                "net_breadth_pct_of_complete"
            ),
        })

    bearish_pe = [
        r
        for r in enriched
        if r["dominant_nifty_regime"] == "BEARISH" and r["right"] == "PE"
    ]
    bearish_ce = [
        r
        for r in enriched
        if r["dominant_nifty_regime"] == "BEARISH" and r["right"] == "CE"
    ]
    bullish_ce = [
        r
        for r in enriched
        if r["dominant_nifty_regime"] == "BULLISH" and r["right"] == "CE"
    ]

    directional_days = [
        day
        for day, r in regimes.items()
        if r.get("regime") in {"BULLISH", "BEARISH"}
    ]
    confirmed_days = [
        day
        for day in directional_days
        if breadth.get(day, {}).get("breadth_direction")
        == regimes[day].get("regime")
    ]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_f5_market_sha256": f5_market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "source_breadth_market_sha256": breadth_market_sha256,
        "breadth_definition": BREADTH_DEFINITION,
        "quality": {
            "spot_sessions": len(spot_by_day),
            "breadth_available_sessions": sum(
                bool(v.get("available")) for v in breadth.values()
            ),
            "baseline_trades": len(selected),
            "matched_trades": len(enriched),
            "directional_regime_days": len(directional_days),
            "breadth_available_directional_days": sum(
                bool(breadth.get(day, {}).get("available"))
                for day in directional_days
            ),
            "breadth_unavailable_directional_days": sum(
                not bool(breadth.get(day, {}).get("available"))
                for day in directional_days
            ),
            "breadth_confirmed_directional_days": len(confirmed_days),
        },
        "daily_breadth": breadth,
        "primary_bearish_analysis": {
            "PE_all": _summary(bearish_pe),
            "PE_breadth_confirmed": _summary([
                r for r in bearish_pe if r["breadth_confirmed"]
            ]),
            "PE_breadth_available_not_confirmed": _summary([
                r for r in bearish_pe
                if r["breadth_available"] and not r["breadth_confirmed"]
            ]),
            "PE_breadth_unavailable": _summary([
                r for r in bearish_pe if not r["breadth_available"]
            ]),
            "CE_all": _summary(bearish_ce),
            "CE_breadth_confirmed": _summary([
                r for r in bearish_ce if r["breadth_confirmed"]
            ]),
            "CE_breadth_available_not_confirmed": _summary([
                r for r in bearish_ce
                if r["breadth_available"] and not r["breadth_confirmed"]
            ]),
            "CE_breadth_unavailable": _summary([
                r for r in bearish_ce if not r["breadth_available"]
            ]),
        },
        "secondary_bullish_analysis": {
            "CE_all": _summary(bullish_ce),
            "CE_breadth_confirmed": _summary([
                r for r in bullish_ce if r["breadth_confirmed"]
            ]),
            "CE_breadth_available_not_confirmed": _summary([
                r for r in bullish_ce
                if r["breadth_available"] and not r["breadth_confirmed"]
            ]),
            "CE_breadth_unavailable": _summary([
                r for r in bullish_ce if not r["breadth_available"]
            ]),
        },
        "by_month": {
            month: _month_report(month, enriched)
            for month in WINDOW["months"]
        },
        "trades": enriched,
        "decision": "NIFTY50_BREADTH_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze NIFTY 50 breadth for F5"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument("--breadth-market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_nifty50_breadth_2026_07_09.json"
        ),
    )
    args = parser.parse_args()

    f5_sha = _sha256(args.f5_market)
    backtest_sha = _sha256(args.backtest)
    breadth_sha = _sha256(args.breadth_market)
    report = analyze(
        json.loads(args.f5_market.read_text(encoding="utf-8")),
        json.loads(args.backtest.read_text(encoding="utf-8")),
        json.loads(args.breadth_market.read_text(encoding="utf-8")),
        f5_market_sha256=f5_sha,
        backtest_sha256=backtest_sha,
        breadth_market_sha256=breadth_sha,
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
        "primary_bearish_analysis": report["primary_bearish_analysis"],
        "secondary_bullish_analysis": report["secondary_bullish_analysis"],
        "by_month": report["by_month"],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
