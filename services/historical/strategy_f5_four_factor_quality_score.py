"""Matched-entry four-factor F5 quality-score diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_conventional_regime_combinations import (
    _entry_key,
    _regime_lookup,
)
from services.historical.strategy_f5_four_factor_quality_score_protocol import (
    GUARDRAILS,
    MONOTONICITY,
    PROTOCOL_VERSION,
    ROLE,
    SCORE_COMPONENTS,
    SCORE_RANGE,
    STRATEGY_ID,
)

RESEARCH_TYPE = "STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_BACKTEST_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pf(values: list[float]) -> float | None:
    pos = sum(v for v in values if v > 0)
    neg = -sum(v for v in values if v < 0)
    if neg == 0.0:
        return None if pos == 0.0 else float("inf")
    return pos / neg


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trades": 0,
            "bad_trades": 0,
            "bad_trade_rate_pct": None,
            "trail_activations": 0,
            "trail_activation_rate_pct": None,
            "baseline_winners": 0,
            "baseline_win_rate_pct": None,
            "net_pnl_inr": 0.0,
            "average_net_pnl_inr": None,
            "median_net_pnl_inr": None,
            "profit_factor": None,
        }

    values = [float(r["baseline_net_pnl_inr"]) for r in rows]
    pf = _pf(values)
    n = len(rows)
    bad = sum(bool(r["bad_trade"]) for r in rows)
    activated = sum(bool(r["trail_activated"]) for r in rows)
    winners = sum(bool(r["baseline_winner"]) for r in rows)
    return {
        "trades": n,
        "bad_trades": bad,
        "bad_trade_rate_pct": round(bad / n * 100.0, 2),
        "trail_activations": activated,
        "trail_activation_rate_pct": round(activated / n * 100.0, 2),
        "baseline_winners": winners,
        "baseline_win_rate_pct": round(winners / n * 100.0, 2),
        "net_pnl_inr": round(sum(values), 2),
        "average_net_pnl_inr": round(sum(values) / n, 2),
        "median_net_pnl_inr": round(float(pd.Series(values).median()), 2),
        "profit_factor": (
            None
            if pf is None
            else ("INF" if math.isinf(pf) else round(float(pf), 4))
        ),
    }


def _matched_rows(
    trades: list[dict[str, Any]],
    lookup: dict[tuple[str, str, int, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for i, trade in enumerate(trades, start=1):
        key = _entry_key(trade)
        features = lookup.get(key)
        if features is None:
            missing.append({
                "trade_number": i,
                "date": str(trade["date"]),
                "entry_timestamp": str(trade["entry_timestamp"]),
                "expiry": str(trade["expiry"]),
                "strike": int(trade["strike"]),
                "right": str(trade["right"]),
            })
            continue

        score_flags = {
            component: bool(features.get(component))
            for component in SCORE_COMPONENTS
        }
        score = sum(int(v) for v in score_flags.values())
        net = float(trade["primary_cost_model"]["net_pnl_inr"])

        rows.append({
            "trade_number": i,
            "date": str(trade["date"]),
            "month": str(trade["month"]),
            "right": str(trade["right"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "expiry": str(trade["expiry"]),
            "strike": int(trade["strike"]),
            "entry_open": float(trade["entry_open"]),
            "baseline_net_pnl_inr": net,
            "trail_activated": bool(trade.get("trail_activated")),
            "baseline_winner": net > 0.0,
            "bad_trade": net < 0.0 and not bool(trade.get("trail_activated")),
            "quality_score": score,
            "score_components": score_flags,
            "atr14_pct": features.get("atr14_pct"),
            "atr14_prior20_median": features.get("atr14_prior20_median"),
            "bb_bandwidth20_pct": features.get("bb_bandwidth20_pct"),
            "bb_prior20_median": features.get("bb_prior20_median"),
            "stoch_d3": features.get("stoch_d3"),
            "macd_hist_pct": features.get("macd_hist_pct"),
        })

    return rows, missing


def _score_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        str(score): _summary([
            r for r in rows if int(r["quality_score"]) == score
        ])
        for score in SCORE_RANGE
    }


def _component_pattern_table(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    patterns: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        flags = row["score_components"]
        pattern = "+".join(
            component
            for component in SCORE_COMPONENTS
            if bool(flags[component])
        ) or "NONE"
        patterns.setdefault(pattern, []).append(row)

    ordered = sorted(
        patterns.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    return {
        name: {
            **_summary(group),
            "quality_score": int(group[0]["quality_score"]),
        }
        for name, group in ordered
    }


def _adjacent_monotonicity(
    score_table: dict[str, Any],
    field: str,
    direction: str,
) -> dict[str, Any]:
    populated = [
        (score, score_table[str(score)][field])
        for score in SCORE_RANGE
        if int(score_table[str(score)]["trades"]) > 0
        and score_table[str(score)][field] is not None
    ]

    comparisons = []
    violations = 0
    for (left_score, left), (right_score, right) in zip(
        populated, populated[1:]
    ):
        if direction == "NON_INCREASING_WITH_SCORE":
            passed = float(right) <= float(left)
        else:
            passed = float(right) >= float(left)
        if not passed:
            violations += 1
        comparisons.append({
            "left_score": left_score,
            "left_value": left,
            "right_score": right_score,
            "right_value": right,
            "passed": passed,
        })

    return {
        "direction": direction,
        "populated_scores": [score for score, _ in populated],
        "adjacent_comparisons": len(comparisons),
        "violations": violations,
        "passed_all_adjacent_comparisons": (
            violations == 0 and len(comparisons) > 0
        ),
        "comparisons": comparisons,
    }


def _slice_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    score_table = _score_table(rows)
    return {
        "overall": _summary(rows),
        "by_score": score_table,
        "monotonicity": {
            "bad_trade_rate": _adjacent_monotonicity(
                score_table,
                "bad_trade_rate_pct",
                MONOTONICITY["bad_trade_rate"],
            ),
            "trail_activation_rate": _adjacent_monotonicity(
                score_table,
                "trail_activation_rate_pct",
                MONOTONICITY["trail_activation_rate"],
            ),
            "baseline_win_rate": _adjacent_monotonicity(
                score_table,
                "baseline_win_rate_pct",
                MONOTONICITY["baseline_win_rate"],
            ),
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
        raise ValueError("F5 market artifact must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != market_sha256:
        raise ValueError("F5 backtest is not bound to supplied market artifact")

    trades = list(
        backtest["candidates"][BASELINE_CANDIDATE].get("trades") or []
    )
    bars_2m, incomplete = _aggregate_2m(
        list(market.get("option_rows_1m") or [])
    )
    lookup = _regime_lookup(bars_2m)
    rows, missing = _matched_rows(trades, lookup)

    by_month = {
        month: _slice_report([
            r for r in rows if r["month"] == month
        ])
        for month in ("2026-07", "2026-08", "2026-09")
    }
    by_side = {
        side: _slice_report([
            r for r in rows if r["right"] == side
        ])
        for side in ("CE", "PE")
    }

    component_pass_rates = {
        component: {
            "trades_with_component": sum(
                bool(r["score_components"][component]) for r in rows
            ),
            "pct_with_component": (
                round(
                    sum(bool(r["score_components"][component]) for r in rows)
                    / len(rows)
                    * 100.0,
                    2,
                )
                if rows
                else None
            ),
        }
        for component in SCORE_COMPONENTS
    }

    overall = _slice_report(rows)

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "baseline_protocol_version": F5_PROTOCOL_VERSION,
        "baseline_candidate": BASELINE_CANDIDATE,
        "score_components": SCORE_COMPONENTS,
        "quality": {
            "baseline_trades": len(trades),
            "matched_rows": len(rows),
            "missing_rows": len(missing),
            "feature_match_coverage_pct": (
                round(len(rows) / len(trades) * 100.0, 2)
                if trades
                else None
            ),
            "complete_2m_bars_rebuilt": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
        },
        "component_pass_rates": component_pass_rates,
        "overall": overall,
        "by_month": by_month,
        "by_side": by_side,
        "component_patterns": _component_pattern_table(rows),
        "matched_rows": rows,
        "missing_feature_matches": missing,
        "decision": "FOUR_FACTOR_QUALITY_SCORE_DIAGNOSTIC_COMPLETE_NO_CUTOFF_PROMOTED",
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose F5 matched-entry four-factor quality score"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_four_factor_quality_score_2026_07_09.json"
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
        "source_market_sha256": market_sha,
        "source_backtest_sha256": backtest_sha,
        "quality": report["quality"],
        "component_pass_rates": report["component_pass_rates"],
        "overall_by_score": report["overall"]["by_score"],
        "overall_monotonicity": report["overall"]["monotonicity"],
        "month_monotonicity": {
            month: block["monotonicity"]
            for month, block in report["by_month"].items()
        },
        "side_monotonicity": {
            side: block["monotonicity"]
            for side, block in report["by_side"].items()
        },
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
