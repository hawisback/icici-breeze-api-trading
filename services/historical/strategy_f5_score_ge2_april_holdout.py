"""Backtest frozen F5 quality-score>=2 candidate on fresh April 2026 data."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
    _bar_open_lookup,
    _decorate,
    _observations,
    _one_min_open_lookup,
    _summary,
    _trail_trade_simulation,
)
from services.historical.strategy_f5_conventional_regime_combinations import (
    _entry_key,
    _regime_lookup,
)
from services.historical.strategy_f5_score_ge2_april_holdout_protocol import (
    CANDIDATE_NAME,
    ENTRY,
    GUARDRAILS,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    VALIDATION_GATE,
)

RESEARCH_TYPE = "STRATEGY_F5_SCORE_GE2_APRIL_HOLDOUT_BACKTEST_V1"
BASELINE_NAME = "BASELINE_F5_RVI50"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _score(features: dict[str, Any] | None) -> int | None:
    if features is None:
        return None
    components = ENTRY["score_components"]
    return sum(int(bool(features.get(component))) for component in components)


def _candidate_observations(
    obs_by_day: dict[str, dict[str, dict[str, Any]]],
    regime_lookup: dict[tuple[str, str, int, str], dict[str, Any]],
) -> tuple[dict[str, dict[str, dict[str, Any]]], int]:
    threshold = int(ENTRY["quality_score_min"])
    result = copy.deepcopy(obs_by_day)
    missing = 0

    for day in result.values():
        for ts, same in day.items():
            for right, obs in same.items():
                if not bool(obs.get("bullish_cross")):
                    continue
                key = (
                    ts,
                    str(obs["expiry"]),
                    int(obs["strike"]),
                    str(right),
                )
                score = _score(regime_lookup.get(key))
                if score is None:
                    missing += 1
                    obs["bullish_cross"] = False
                else:
                    obs["bullish_cross"] = score >= threshold

    return result, missing


def _holdout_report(
    trades: list[dict[str, Any]],
    skips: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "quality": {
            "scorable_trades": len(trades),
            "skipped_trade_intents": len(skips),
        },
        "pooled": {
            key: _summary(trades, key)
            for key in ("0.00", "0.50", "1.00")
        },
        "by_month": {
            "2026-04": {
                key: _summary(
                    [t for t in trades if str(t["month"]) == "2026-04"],
                    key,
                )
                for key in ("0.00", "0.50", "1.00")
            }
        },
        "ce_0_00": _summary(
            [t for t in trades if t["right"] == "CE"], "0.00"
        ),
        "pe_0_00": _summary(
            [t for t in trades if t["right"] == "PE"], "0.00"
        ),
        "exit_reason_counts": {
            reason: sum(str(t["exit_reason"]) == reason for t in trades)
            for reason in sorted({str(t["exit_reason"]) for t in trades})
        },
        "trades": trades,
        "skipped": skips,
    }


def _matched_signal_capture(
    baseline_trades: list[dict[str, Any]],
    regime_lookup: dict[tuple[str, str, int, str], dict[str, Any]],
) -> dict[str, Any]:
    threshold = int(ENTRY["quality_score_min"])
    activated = [
        t for t in baseline_trades if bool(t.get("trail_activated"))
    ]
    winners = [
        t for t in baseline_trades
        if float(t["primary_cost_model"]["net_pnl_inr"]) > 0.0
    ]

    def captured(group: list[dict[str, Any]]) -> tuple[int, float]:
        if not group:
            return 0, 100.0
        n = 0
        for trade in group:
            score = _score(regime_lookup.get(_entry_key(trade)))
            if score is not None and score >= threshold:
                n += 1
        return n, n / len(group) * 100.0

    act_n, act_pct = captured(activated)
    win_n, win_pct = captured(winners)
    return {
        "baseline_activated_signals": len(activated),
        "captured_baseline_activated_signals": act_n,
        "baseline_activated_signal_capture_pct": round(act_pct, 2),
        "baseline_winner_signals": len(winners),
        "captured_baseline_winner_signals": win_n,
        "baseline_winner_signal_capture_pct": round(win_pct, 2),
    }


def _gate(
    baseline_report: dict[str, Any],
    candidate_report: dict[str, Any],
    capture: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    b0 = baseline_report["pooled"]["0.00"]
    c0 = candidate_report["pooled"]["0.00"]
    b05 = baseline_report["pooled"]["0.50"]
    c05 = candidate_report["pooled"]["0.50"]
    b10 = baseline_report["pooled"]["1.00"]
    c10 = candidate_report["pooled"]["1.00"]

    if int(c0["trades"]) < int(VALIDATION_GATE["minimum_candidate_trades"]):
        failures.append("candidate_trade_count_below_minimum")

    for side, block in (
        ("CE", candidate_report["ce_0_00"]),
        ("PE", candidate_report["pe_0_00"]),
    ):
        if int(block["trades"]) < int(
            VALIDATION_GATE["minimum_candidate_trades_each_side"]
        ):
            failures.append(f"candidate_{side}_trade_count_below_minimum")

    if int(capture["baseline_activated_signals"]) < int(
        VALIDATION_GATE["minimum_baseline_trail_activations"]
    ):
        failures.append("baseline_trail_activation_count_below_minimum")

    if float(capture["baseline_activated_signal_capture_pct"]) < float(
        VALIDATION_GATE["minimum_baseline_activated_signal_capture_pct"]
    ):
        failures.append("baseline_activated_signal_capture_below_minimum")

    if float(capture["baseline_winner_signal_capture_pct"]) < float(
        VALIDATION_GATE["minimum_baseline_winner_signal_capture_pct"]
    ):
        failures.append("baseline_winner_signal_capture_below_minimum")

    if float(c0["trail_activation_rate_pct"]) <= float(
        b0["trail_activation_rate_pct"]
    ):
        failures.append("candidate_activation_rate_not_above_baseline")

    if float(c0["net_pnl_inr"]) <= 0.0:
        failures.append("candidate_zero_slippage_net_not_positive")

    pf = c0["profit_factor"]
    if pf is None or (pf != "INF" and float(pf) <= 1.0):
        failures.append("candidate_zero_slippage_profit_factor_not_above_one")

    if float(c05["net_pnl_inr"]) <= 0.0:
        failures.append("candidate_half_point_net_not_positive")

    deltas: dict[str, float] = {}
    for key, b, c in (
        ("0.00", b0, c0),
        ("0.50", b05, c05),
        ("1.00", b10, c10),
    ):
        delta = float(c["net_pnl_inr"]) - float(b["net_pnl_inr"])
        deltas[key] = round(delta, 2)
        if delta <= 0.0:
            failures.append(f"candidate_net_not_above_baseline_{key}")

    if float(c0["max_drawdown_inr"]) >= float(b0["max_drawdown_inr"]):
        failures.append("candidate_max_drawdown_not_below_baseline")

    return {
        "passed": not failures,
        "failures": failures,
        "matched_signal_capture": capture,
        "pooled_net_delta_vs_baseline_inr": deltas,
        "contract": VALIDATION_GATE,
    }


def backtest(
    payload: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("April holdout market artifact protocol mismatch")
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("April holdout market artifact must be outcome-unscored")

    contracts = list(payload.get("daily_contracts") or [])
    rows_1m = list(payload.get("option_rows_1m") or [])
    bars_2m, incomplete = _aggregate_2m(rows_1m)
    obs, insufficient = _observations(contracts, bars_2m)
    regime_lookup = _regime_lookup(bars_2m)
    candidate_obs, missing_candidate_features = _candidate_observations(
        obs, regime_lookup
    )
    open_2m = _bar_open_lookup(bars_2m)
    open_1m = _one_min_open_lookup(rows_1m)

    baseline_trades, baseline_skips = _trail_trade_simulation(
        contracts, obs, open_2m, open_1m
    )
    candidate_trades, candidate_skips = _trail_trade_simulation(
        contracts, candidate_obs, open_2m, open_1m
    )
    baseline_trades = _decorate(baseline_trades)
    candidate_trades = _decorate(candidate_trades)

    baseline_report = _holdout_report(baseline_trades, baseline_skips)
    candidate_report = _holdout_report(candidate_trades, candidate_skips)
    capture = _matched_signal_capture(baseline_trades, regime_lookup)
    gate = _gate(baseline_report, candidate_report, capture)

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "candidate_name": CANDIDATE_NAME,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "entry_rule": ENTRY,
        "quality": {
            "selected_sessions": len(contracts),
            "raw_1m_option_rows": len(rows_1m),
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
            "missing_candidate_regime_rows": missing_candidate_features,
        },
        "baseline": baseline_report,
        "candidate": candidate_report,
        "validation_gate": gate,
        "decision": (
            "F5_SCORE_GE2_APRIL_HOLDOUT_PASSED_REQUIRES_SECOND_HOLDOUT"
            if gate["passed"]
            else "F5_SCORE_GE2_APRIL_HOLDOUT_FAILED_DO_NOT_TUNE_ON_APRIL"
        ),
        "insufficient_warmup": insufficient,
        "guardrails": GUARDRAILS,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest frozen F5 score>=2 candidate on April holdout"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_score_ge2_april_holdout_2026_04.json"
        ),
    )
    args = parser.parse_args()

    source_sha = _sha256(args.market)
    payload = json.loads(args.market.read_text(encoding="utf-8"))
    report = backtest(payload, source_sha256=source_sha)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = _sha256(args.output)

    def compact(block: dict[str, Any]) -> dict[str, Any]:
        return {
            "pooled": block["pooled"],
            "ce_0_00": block["ce_0_00"],
            "pe_0_00": block["pe_0_00"],
            "exit_reason_counts": block["exit_reason_counts"],
            "quality": block["quality"],
        }

    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_market_sha256": source_sha,
        "quality": report["quality"],
        "baseline": compact(report["baseline"]),
        "candidate": compact(report["candidate"]),
        "validation_gate": report["validation_gate"],
        "decision": report["decision"],
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
