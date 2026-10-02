"""Analyze prior-session institutional participant OI against F5 outcomes."""
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
from services.historical.strategy_f5_lagged_institutional_oi_protocol import (
    CHANGE_STATE,
    GUARDRAILS,
    POSITION_STATE,
    PROTOCOL_VERSION,
    ROLE,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_BACKTEST_V1"
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


def _position_state(net_value: int | float) -> str:
    if net_value > 0:
        return "NET_LONG"
    if net_value < 0:
        return "NET_SHORT"
    return "FLAT"


def _change_state(change: int | float) -> str:
    if change > 0:
        return "MORE_LONG"
    if change < 0:
        return "MORE_SHORT"
    return "FLAT"


def _participant_metrics(row: dict[str, Any]) -> dict[str, Any]:
    long_fut = int(row["future_index_long"])
    short_fut = int(row["future_index_short"])
    net_fut = long_fut - short_fut
    total_fut = long_fut + short_fut

    call_long = int(row["option_index_call_long"])
    put_long = int(row["option_index_put_long"])
    call_short = int(row["option_index_call_short"])
    put_short = int(row["option_index_put_short"])
    option_balance = (call_long + put_short) - (call_short + put_long)

    return {
        "index_futures_long": long_fut,
        "index_futures_short": short_fut,
        "index_futures_net": net_fut,
        "index_futures_state": _position_state(net_fut),
        "index_futures_long_share_pct": (
            None
            if total_fut == 0
            else round(long_fut / total_fut * 100.0, 2)
        ),
        "index_options_directional_balance": option_balance,
        "index_options_directional_balance_note": (
            "DESCRIPTIVE_PROXY_NOT_DELTA_OR_GAMMA"
        ),
    }


def _context_by_session(
    trading_sessions: list[date],
    institutional_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_date_participant: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in institutional_rows:
        by_date_participant[str(row["date"])][str(row["participant"])] = row

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
        prior2 = trading_sessions[i - 2] if i >= 2 else None
        current_rows = by_date_participant.get(prior.isoformat(), {})
        earlier_rows = (
            {}
            if prior2 is None
            else by_date_participant.get(prior2.isoformat(), {})
        )

        participants: dict[str, Any] = {}
        for participant in ("FII", "DII"):
            current = current_rows.get(participant)
            if current is None:
                participants[participant] = {
                    "available": False,
                    "reason": "PRIOR_REPORT_PARTICIPANT_MISSING",
                }
                continue
            metrics = _participant_metrics(current)
            earlier = earlier_rows.get(participant)
            if earlier is None:
                change = None
                change_state = None
            else:
                earlier_metrics = _participant_metrics(earlier)
                change = (
                    int(metrics["index_futures_net"])
                    - int(earlier_metrics["index_futures_net"])
                )
                change_state = _change_state(change)
            participants[participant] = {
                "available": True,
                **metrics,
                "index_futures_net_change": change,
                "index_futures_change_state": change_state,
            }

        fii_available = bool(participants.get("FII", {}).get("available"))
        dii_available = bool(participants.get("DII", {}).get("available"))
        result[day_key] = {
            "available": fii_available and dii_available,
            "prior_report_date": prior.isoformat(),
            "prior2_report_date": (
                None if prior2 is None else prior2.isoformat()
            ),
            "FII": participants.get("FII"),
            "DII": participants.get("DII"),
        }
    return result


def _state_summary(
    rows: list[dict[str, Any]],
    *,
    participant: str,
    state_field: str,
) -> dict[str, Any]:
    states = (
        POSITION_STATE.keys()
        if state_field == "index_futures_state"
        else CHANGE_STATE.keys()
    )
    return {
        state: {
            side: _summary([
                r
                for r in rows
                if r.get(f"{participant}_{state_field}") == state
                and r["right"] == side
            ])
            for side in ("CE", "PE")
        }
        for state in states
    }


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
        "all_trades": _summary(group),
        "bearish_regime": {
            "PE_all": _summary(bearish_pe),
            "PE_prior_FII_net_short": _summary([
                r for r in bearish_pe
                if r.get("FII_index_futures_state") == "NET_SHORT"
            ]),
            "PE_prior_FII_not_net_short": _summary([
                r for r in bearish_pe
                if r.get("FII_index_futures_state") not in {None, "NET_SHORT"}
            ]),
            "PE_prior_FII_more_short": _summary([
                r for r in bearish_pe
                if r.get("FII_index_futures_change_state") == "MORE_SHORT"
            ]),
            "PE_prior_FII_not_more_short": _summary([
                r for r in bearish_pe
                if r.get("FII_index_futures_change_state")
                not in {None, "MORE_SHORT"}
            ]),
            "CE_prior_FII_net_short": _summary([
                r for r in bearish_ce
                if r.get("FII_index_futures_state") == "NET_SHORT"
            ]),
            "CE_prior_FII_more_short": _summary([
                r for r in bearish_ce
                if r.get("FII_index_futures_change_state") == "MORE_SHORT"
            ]),
        },
        "bullish_regime": {
            "CE_all": _summary(bullish_ce),
            "CE_prior_FII_net_long": _summary([
                r for r in bullish_ce
                if r.get("FII_index_futures_state") == "NET_LONG"
            ]),
            "CE_prior_FII_more_long": _summary([
                r for r in bullish_ce
                if r.get("FII_index_futures_change_state") == "MORE_LONG"
            ]),
        },
    }


def analyze(
    f5_market: dict[str, Any],
    backtest: dict[str, Any],
    institutional_market: dict[str, Any],
    *,
    f5_market_sha256: str,
    backtest_sha256: str,
    institutional_market_sha256: str,
) -> dict[str, Any]:
    if f5_market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if f5_market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != f5_market_sha256:
        raise ValueError("F5 backtest not bound to supplied market")
    if institutional_market.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("institutional market protocol mismatch")
    if institutional_market.get("source_f5_market_sha256") != f5_market_sha256:
        raise ValueError("institutional market not bound to supplied F5 market")

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])

    spot_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_sessions = sorted({
        date.fromisoformat(str(row["date"]))
        for row in list(f5_market.get("spot_rows") or [])
    })
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
        list(institutional_market.get("participant_oi_rows") or []),
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
            "institutional_context_available": bool(context.get("available")),
            "prior_report_date": context.get("prior_report_date"),
            "FII_index_futures_state": fii.get("index_futures_state"),
            "FII_index_futures_net": fii.get("index_futures_net"),
            "FII_index_futures_change_state": fii.get(
                "index_futures_change_state"
            ),
            "FII_index_futures_net_change": fii.get(
                "index_futures_net_change"
            ),
            "FII_index_options_directional_balance": fii.get(
                "index_options_directional_balance"
            ),
            "DII_index_futures_state": dii.get("index_futures_state"),
            "DII_index_futures_net": dii.get("index_futures_net"),
            "DII_index_futures_change_state": dii.get(
                "index_futures_change_state"
            ),
            "DII_index_futures_net_change": dii.get(
                "index_futures_net_change"
            ),
            "DII_index_options_directional_balance": dii.get(
                "index_options_directional_balance"
            ),
        })

    available = [r for r in enriched if r["institutional_context_available"]]
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
        "source_institutional_market_sha256": institutional_market_sha256,
        "position_state_definition": POSITION_STATE,
        "change_state_definition": CHANGE_STATE,
        "quality": {
            "target_sessions": len(target_days),
            "sessions_with_lagged_context": sum(
                bool(contexts.get(day, {}).get("available"))
                for day in target_days
            ),
            "baseline_trades": len(selected),
            "trades_with_lagged_context": len(available),
        },
        "daily_lagged_context": {
            day: contexts.get(day)
            for day in target_days
        },
        "primary_bearish_analysis": {
            "PE_all_with_context": _summary(bearish_pe),
            "CE_all_with_context": _summary(bearish_ce),
            "PE_prior_FII_net_short": _summary([
                r for r in bearish_pe
                if r.get("FII_index_futures_state") == "NET_SHORT"
            ]),
            "PE_prior_FII_not_net_short": _summary([
                r for r in bearish_pe
                if r.get("FII_index_futures_state") != "NET_SHORT"
            ]),
            "CE_prior_FII_net_short": _summary([
                r for r in bearish_ce
                if r.get("FII_index_futures_state") == "NET_SHORT"
            ]),
            "CE_prior_FII_not_net_short": _summary([
                r for r in bearish_ce
                if r.get("FII_index_futures_state") != "NET_SHORT"
            ]),
            "PE_prior_FII_more_short": _summary([
                r for r in bearish_pe
                if r.get("FII_index_futures_change_state") == "MORE_SHORT"
            ]),
            "PE_prior_FII_not_more_short": _summary([
                r for r in bearish_pe
                if r.get("FII_index_futures_change_state") != "MORE_SHORT"
            ]),
            "CE_prior_FII_more_short": _summary([
                r for r in bearish_ce
                if r.get("FII_index_futures_change_state") == "MORE_SHORT"
            ]),
            "CE_prior_FII_not_more_short": _summary([
                r for r in bearish_ce
                if r.get("FII_index_futures_change_state") != "MORE_SHORT"
            ]),
        },
        "secondary_bullish_analysis": {
            "CE_all_with_context": _summary(bullish_ce),
            "CE_prior_FII_net_long": _summary([
                r for r in bullish_ce
                if r.get("FII_index_futures_state") == "NET_LONG"
            ]),
            "CE_prior_FII_not_net_long": _summary([
                r for r in bullish_ce
                if r.get("FII_index_futures_state") != "NET_LONG"
            ]),
            "CE_prior_FII_more_long": _summary([
                r for r in bullish_ce
                if r.get("FII_index_futures_change_state") == "MORE_LONG"
            ]),
            "CE_prior_FII_not_more_long": _summary([
                r for r in bullish_ce
                if r.get("FII_index_futures_change_state") != "MORE_LONG"
            ]),
        },
        "FII_by_position_state": _state_summary(
            available,
            participant="FII",
            state_field="index_futures_state",
        ),
        "FII_by_change_state": _state_summary(
            available,
            participant="FII",
            state_field="index_futures_change_state",
        ),
        "DII_by_position_state": _state_summary(
            available,
            participant="DII",
            state_field="index_futures_state",
        ),
        "DII_by_change_state": _state_summary(
            available,
            participant="DII",
            state_field="index_futures_change_state",
        ),
        "by_month": {
            month: _month_report(month, available)
            for month in WINDOW["months"]
        },
        "trades": enriched,
        "decision": "LAGGED_INSTITUTIONAL_OI_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze lagged institutional participant OI for F5"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument("--institutional-market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_lagged_institutional_oi_2026_07_09.json"
        ),
    )
    args = parser.parse_args()

    f5_sha = _sha256(args.f5_market)
    backtest_sha = _sha256(args.backtest)
    institutional_sha = _sha256(args.institutional_market)
    report = analyze(
        json.loads(args.f5_market.read_text(encoding="utf-8")),
        json.loads(args.backtest.read_text(encoding="utf-8")),
        json.loads(args.institutional_market.read_text(encoding="utf-8")),
        f5_market_sha256=f5_sha,
        backtest_sha256=backtest_sha,
        institutional_market_sha256=institutional_sha,
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
