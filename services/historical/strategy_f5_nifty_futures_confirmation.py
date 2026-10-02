"""Analyze NIFTY futures price/OI context against F5 outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_dominant_nifty_regime import _classify_day
from services.historical.strategy_f5_nifty_futures_confirmation_protocol import (
    FUTURES_STATE,
    GUARDRAILS,
    PROTOCOL_VERSION,
    ROLE,
    STRICT_BUILD_CONFIRMATION,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_BACKTEST_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sign(value: float, *, eps: float = 1e-12) -> str:
    if value > eps:
        return "UP"
    if value < -eps:
        return "DOWN"
    return "FLAT"


def _row_at(rows: list[dict[str, Any]], hhmm: str) -> dict[str, Any] | None:
    for row in rows:
        ts = datetime.fromisoformat(str(row["timestamp"]))
        if ts.strftime("%H:%M") == hhmm:
            return row
    return None


def _futures_state(
    day: str,
    futures_rows: list[dict[str, Any]],
    spot_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    frows = sorted(futures_rows, key=lambda r: str(r["timestamp"]))
    srows = sorted(spot_rows, key=lambda r: str(r["timestamp"]))
    f_open = _row_at(frows, str(WINDOW["session_start"]))
    f_end = _row_at(frows, str(WINDOW["context_end"]))
    s_open = _row_at(srows, str(WINDOW["session_start"]))
    s_end = _row_at(srows, str(WINDOW["context_end"]))

    if f_open is None or f_end is None:
        return {
            "date": day,
            "available": False,
            "state": "FLAT_OR_MISSING",
            "reason": "MISSING_FUTURES_0915_OR_1520",
        }

    p0 = float(f_open["open"])
    p1 = float(f_end["open"])
    price_return_pct = 100.0 * (p1 / p0 - 1.0)
    price_dir = _sign(price_return_pct)

    oi0_raw = f_open.get("open_interest")
    oi1_raw = f_end.get("open_interest")
    oi_available = oi0_raw is not None and oi1_raw is not None
    if oi_available:
        oi0 = float(oi0_raw)
        oi1 = float(oi1_raw)
        oi_change = oi1 - oi0
        oi_change_pct = None if oi0 == 0 else 100.0 * oi_change / oi0
        oi_dir = _sign(oi_change)
    else:
        oi0 = oi1 = oi_change = oi_change_pct = None
        oi_dir = "MISSING"

    if price_dir == "UP" and oi_dir == "UP":
        state = "LONG_BUILDUP"
    elif price_dir == "DOWN" and oi_dir == "UP":
        state = "SHORT_BUILDUP"
    elif price_dir == "UP" and oi_dir == "DOWN":
        state = "SHORT_COVERING"
    elif price_dir == "DOWN" and oi_dir == "DOWN":
        state = "LONG_UNWINDING"
    else:
        state = "FLAT_OR_MISSING"

    opening_basis = end_basis = basis_change = None
    if s_open is not None and s_end is not None:
        opening_basis = p0 - float(s_open["open"])
        end_basis = p1 - float(s_end["open"])
        basis_change = end_basis - opening_basis

    included = [
        r
        for r in frows
        if str(WINDOW["session_start"])
        <= datetime.fromisoformat(str(r["timestamp"])).strftime("%H:%M")
        <= str(WINDOW["context_end"])
    ]
    volumes = [
        float(r["volume"]) for r in included if r.get("volume") is not None
    ]

    return {
        "date": day,
        "available": True,
        "expiry": str(f_open["expiry"]),
        "state": state,
        "futures_0915_open": round(p0, 4),
        "futures_1520_open": round(p1, 4),
        "futures_price_return_pct": round(price_return_pct, 4),
        "price_direction": price_dir,
        "oi_available": oi_available,
        "oi_0915": oi0,
        "oi_1520": oi1,
        "oi_change": oi_change,
        "oi_change_pct": (
            None if oi_change_pct is None else round(oi_change_pct, 4)
        ),
        "oi_direction": oi_dir,
        "opening_basis_points": (
            None if opening_basis is None else round(opening_basis, 4)
        ),
        "basis_1520_points": (
            None if end_basis is None else round(end_basis, 4)
        ),
        "basis_change_points": (
            None if basis_change is None else round(basis_change, 4)
        ),
        "futures_volume_through_1520": (
            round(sum(volumes), 2) if volumes else None
        ),
        "bars_through_1520": len(included),
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


def _nested_state_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        regime: {
            state: {
                side: _summary([
                    r
                    for r in rows
                    if r["dominant_nifty_regime"] == regime
                    and r["futures_state"] == state
                    and r["right"] == side
                ])
                for side in ("CE", "PE")
            }
            for state in FUTURES_STATE
        }
        for regime in ("BULLISH", "BEARISH", "MIXED")
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
    short_build_pe = [
        r for r in bearish_pe if r["futures_state"] == "SHORT_BUILDUP"
    ]
    non_short_build_pe = [
        r for r in bearish_pe if r["futures_state"] != "SHORT_BUILDUP"
    ]
    return {
        "all_trades": _summary(group),
        "bearish_regime": {
            "CE": _summary(bearish_ce),
            "PE": _summary(bearish_pe),
            "PE_short_buildup": _summary(short_build_pe),
            "PE_non_short_buildup": _summary(non_short_build_pe),
        },
        "by_regime_futures_state_side": _nested_state_report(group),
    }


def analyze(
    f5_market: dict[str, Any],
    backtest: dict[str, Any],
    futures_market: dict[str, Any],
    *,
    f5_market_sha256: str,
    backtest_sha256: str,
    futures_market_sha256: str,
) -> dict[str, Any]:
    if f5_market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if f5_market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != f5_market_sha256:
        raise ValueError("F5 backtest not bound to supplied market")
    if futures_market.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("futures context market protocol mismatch")
    if futures_market.get("source_f5_market_sha256") != f5_market_sha256:
        raise ValueError("futures context not bound to supplied F5 market")

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])

    spot_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in list(f5_market.get("spot_rows") or []):
        d = date.fromisoformat(str(row["date"]))
        if start <= d <= end:
            spot_by_day[d.isoformat()].append(row)

    fut_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in list(futures_market.get("futures_rows_5m") or []):
        d = date.fromisoformat(str(row["date"]))
        if start <= d <= end:
            fut_by_day[d.isoformat()].append(row)

    regimes = {
        day: _classify_day(day, rows)
        for day, rows in sorted(spot_by_day.items())
    }
    futures_context = {
        day: _futures_state(
            day,
            fut_by_day.get(day, []),
            spot_by_day.get(day, []),
        )
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
        fut = futures_context.get(day, {})
        state = str(fut.get("state") or "FLAT_OR_MISSING")
        pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
        strict_confirm = (
            regime in STRICT_BUILD_CONFIRMATION
            and state == STRICT_BUILD_CONFIRMATION[regime]
        )
        enriched.append({
            "date": day,
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "right": str(trade["right"]),
            "trail_activated": bool(trade.get("trail_activated")),
            "net_pnl_inr": pnl,
            "winner": pnl > 0.0,
            "dominant_nifty_regime": regime,
            "futures_state": state,
            "strict_futures_build_confirmation": strict_confirm,
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

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_f5_market_sha256": f5_market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "source_futures_market_sha256": futures_market_sha256,
        "futures_state_definition": FUTURES_STATE,
        "strict_build_confirmation": STRICT_BUILD_CONFIRMATION,
        "quality": {
            "spot_sessions": len(spot_by_day),
            "futures_sessions": len(fut_by_day),
            "futures_context_available_sessions": sum(
                bool(v.get("available")) for v in futures_context.values()
            ),
            "futures_sessions_with_oi": sum(
                bool(v.get("oi_available")) for v in futures_context.values()
            ),
            "baseline_trades": len(selected),
            "matched_trades": len(enriched),
        },
        "daily_futures_context": futures_context,
        "primary_bearish_analysis": {
            "PE_all": _summary(bearish_pe),
            "CE_all": _summary(bearish_ce),
            "PE_short_buildup": _summary([
                r for r in bearish_pe if r["futures_state"] == "SHORT_BUILDUP"
            ]),
            "PE_non_short_buildup": _summary([
                r for r in bearish_pe if r["futures_state"] != "SHORT_BUILDUP"
            ]),
            "CE_short_buildup": _summary([
                r for r in bearish_ce if r["futures_state"] == "SHORT_BUILDUP"
            ]),
            "CE_non_short_buildup": _summary([
                r for r in bearish_ce if r["futures_state"] != "SHORT_BUILDUP"
            ]),
        },
        "secondary_bullish_analysis": {
            "CE_all": _summary(bullish_ce),
            "CE_long_buildup": _summary([
                r for r in bullish_ce if r["futures_state"] == "LONG_BUILDUP"
            ]),
            "CE_non_long_buildup": _summary([
                r for r in bullish_ce if r["futures_state"] != "LONG_BUILDUP"
            ]),
        },
        "by_regime_futures_state_side": _nested_state_report(enriched),
        "by_month": {
            month: _month_report(month, enriched)
            for month in WINDOW["months"]
        },
        "trades": enriched,
        "decision": "NIFTY_FUTURES_CONFIRMATION_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze NIFTY futures confirmation for F5"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument("--futures-market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_nifty_futures_confirmation_2026_07_09.json"
        ),
    )
    args = parser.parse_args()

    f5_sha = _sha256(args.f5_market)
    backtest_sha = _sha256(args.backtest)
    futures_sha = _sha256(args.futures_market)
    report = analyze(
        json.loads(args.f5_market.read_text(encoding="utf-8")),
        json.loads(args.backtest.read_text(encoding="utf-8")),
        json.loads(args.futures_market.read_text(encoding="utf-8")),
        f5_market_sha256=f5_sha,
        backtest_sha256=backtest_sha,
        futures_market_sha256=futures_sha,
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
