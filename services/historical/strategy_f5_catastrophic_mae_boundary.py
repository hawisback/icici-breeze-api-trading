"""Derive a catastrophic stop boundary from F5 pre-trail 1m MAE."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_dominant_nifty_regime import _classify_day
from services.historical.strategy_f5_catastrophic_mae_boundary_protocol import (
    DEVELOPMENT_WINDOW,
    GUARDRAILS,
    PRESERVATION_TARGET_PCT,
    PROTOCOL_VERSION,
    QUANTILE,
    ROLE,
    STRATEGY_ID,
)

RESEARCH_TYPE = "STRATEGY_F5_CATASTROPHIC_MAE_BOUNDARY_DIAGNOSTIC_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _option_rows_for_trade(
    rows: list[dict[str, Any]],
    trade: dict[str, Any],
) -> list[dict[str, Any]]:
    entry = datetime.fromisoformat(str(trade["entry_timestamp"]))
    exit_dt = datetime.fromisoformat(str(trade["exit_timestamp"]))
    activation_text = trade.get("trail_activation_timestamp")
    boundary = (
        datetime.fromisoformat(str(activation_text))
        if activation_text
        else exit_dt
    )
    return sorted(
        [
            row
            for row in rows
            if str(row["date"]) == str(trade["date"])
            and str(row["expiry"]) == str(trade["expiry"])
            and int(row["strike"]) == int(trade["strike"])
            and str(row["right"]) == str(trade["right"])
            and entry
            <= datetime.fromisoformat(str(row["timestamp"]))
            < boundary
        ],
        key=lambda row: str(row["timestamp"]),
    )


def _trade_mae(
    rows: list[dict[str, Any]],
    trade: dict[str, Any],
) -> dict[str, Any]:
    path = _option_rows_for_trade(rows, trade)
    entry_open = float(trade["entry_open"])
    if not path:
        mae = 0.0
        mfe = 0.0
    else:
        mae = min(
            (float(row["low"]) / entry_open - 1.0) * 100.0
            for row in path
        )
        mfe = max(
            (float(row["high"]) / entry_open - 1.0) * 100.0
            for row in path
        )
    pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
    return {
        "date": str(trade["date"]),
        "month": str(trade["month"]),
        "entry_timestamp": str(trade["entry_timestamp"]),
        "exit_timestamp": str(trade["exit_timestamp"]),
        "trail_activation_timestamp": trade.get("trail_activation_timestamp"),
        "winner": pnl > 0.0,
        "trail_activated": bool(trade.get("trail_activated")),
        "baseline_net_pnl_inr": pnl,
        "pretrail_1m_bar_count": len(path),
        "pretrail_mae_low_return_pct": round(float(mae), 6),
        "pretrail_mfe_high_return_pct": round(float(mfe), 6),
    }


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "p01": None,
            "p05": None,
            "p10": None,
            "p25": None,
            "p50": None,
            "minimum": None,
            "maximum": None,
        }
    arr = np.asarray(values, dtype=float)
    return {
        "count": len(values),
        "p01": round(float(np.quantile(arr, 0.01)), 4),
        "p05": round(float(np.quantile(arr, 0.05)), 4),
        "p10": round(float(np.quantile(arr, 0.10)), 4),
        "p25": round(float(np.quantile(arr, 0.25)), 4),
        "p50": round(float(np.quantile(arr, 0.50)), 4),
        "minimum": round(float(np.min(arr)), 4),
        "maximum": round(float(np.max(arr)), 4),
    }


def _candidate_distance(
    winner_values: list[float],
    activated_values: list[float],
) -> dict[str, Any]:
    if not winner_values or not activated_values:
        return {
            "available": False,
            "reason": "MISSING_SUCCESSFUL_TRADE_GROUP",
        }
    winner_p05 = float(np.quantile(np.asarray(winner_values), QUANTILE))
    activated_p05 = float(
        np.quantile(np.asarray(activated_values), QUANTILE)
    )
    # MAE values are negative. The more negative boundary is farther away and
    # therefore more conservative for successful-trade preservation.
    chosen_mae = min(winner_p05, activated_p05)
    stop_distance = abs(chosen_mae)
    winner_breaches = sum(value <= -stop_distance for value in winner_values)
    activated_breaches = sum(
        value <= -stop_distance for value in activated_values
    )
    winner_preservation = (
        (len(winner_values) - winner_breaches) / len(winner_values) * 100.0
    )
    activated_preservation = (
        (len(activated_values) - activated_breaches)
        / len(activated_values)
        * 100.0
    )
    return {
        "available": True,
        "derivation": "MAX_DISTANCE_OF_WINNER_P05_AND_ACTIVATED_P05",
        "winner_p05_mae_pct": round(winner_p05, 4),
        "activated_p05_mae_pct": round(activated_p05, 4),
        "candidate_stop_distance_pct": round(stop_distance, 4),
        "candidate_stop_return_pct": round(-stop_distance, 4),
        "development_winner_preservation_pct_at_boundary": round(
            winner_preservation, 2
        ),
        "development_activation_preservation_pct_at_boundary": round(
            activated_preservation, 2
        ),
        "preservation_target_pct": PRESERVATION_TARGET_PCT,
        "not_pnl_optimized": True,
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
        raise ValueError("F5 backtest not bound to supplied market")

    start = date.fromisoformat(DEVELOPMENT_WINDOW["start"])
    end = date.fromisoformat(DEVELOPMENT_WINDOW["end"])
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
    target = [
        trade
        for trade in baseline
        if start <= date.fromisoformat(str(trade["date"])) <= end
        and str(trade["right"]) == "PE"
        and regimes.get(str(trade["date"]), {}).get("regime") == "BEARISH"
    ]
    option_rows = list(market.get("option_rows_1m") or [])
    diagnostics = [_trade_mae(option_rows, trade) for trade in target]

    winner_values = [
        float(row["pretrail_mae_low_return_pct"])
        for row in diagnostics
        if row["winner"]
    ]
    loser_values = [
        float(row["pretrail_mae_low_return_pct"])
        for row in diagnostics
        if not row["winner"]
    ]
    activated_values = [
        float(row["pretrail_mae_low_return_pct"])
        for row in diagnostics
        if row["trail_activated"]
    ]
    nonactivated_values = [
        float(row["pretrail_mae_low_return_pct"])
        for row in diagnostics
        if not row["trail_activated"]
    ]

    monthly = {}
    for month in DEVELOPMENT_WINDOW["months"]:
        group = [row for row in diagnostics if row["month"] == month]
        monthly[month] = {
            "winners": _distribution([
                float(row["pretrail_mae_low_return_pct"])
                for row in group if row["winner"]
            ]),
            "trail_activated": _distribution([
                float(row["pretrail_mae_low_return_pct"])
                for row in group if row["trail_activated"]
            ]),
            "nonwinners": _distribution([
                float(row["pretrail_mae_low_return_pct"])
                for row in group if not row["winner"]
            ]),
        }

    candidate = _candidate_distance(winner_values, activated_values)
    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "quality": {
            "target_bearish_PE_trades": len(target),
            "diagnostic_rows": len(diagnostics),
            "rows_with_empty_pretrail_path": sum(
                int(row["pretrail_1m_bar_count"]) == 0
                for row in diagnostics
            ),
        },
        "pretrail_intrabar_mae": {
            "baseline_winners": _distribution(winner_values),
            "baseline_nonwinners": _distribution(loser_values),
            "trail_activated": _distribution(activated_values),
            "not_trail_activated": _distribution(nonactivated_values),
        },
        "by_month": monthly,
        "derived_single_candidate": candidate,
        "trade_rows": diagnostics,
        "decision": (
            "CATASTROPHIC_MAE_BOUNDARY_DERIVED_REQUIRES_FRESH_HOLDOUT"
            if candidate.get("available")
            else "CATASTROPHIC_MAE_BOUNDARY_NOT_DERIVABLE"
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive F5 catastrophic stop from bearish-PE 1m MAE"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_catastrophic_mae_boundary_2026_07_09.json"
        ),
    )
    args = parser.parse_args()

    market_sha = _sha256(args.market)
    backtest_sha = _sha256(args.backtest)
    report = analyze(
        json.loads(args.market.read_text(encoding="utf-8")),
        json.loads(args.backtest.read_text(encoding="utf-8")),
        market_sha256=market_sha,
        backtest_sha256=backtest_sha,
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
        "pretrail_intrabar_mae": report["pretrail_intrabar_mae"],
        "by_month": report["by_month"],
        "derived_single_candidate": report["derived_single_candidate"],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
