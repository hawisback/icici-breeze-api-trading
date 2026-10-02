"""Validate the frozen 14:30 late bearish-PE risk candidate on June."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Any

import numpy as np

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
    _bar_open_lookup,
    _decorate,
    _observations,
    _one_min_open_lookup,
    _summary,
    _trail_trade_simulation,
)
from services.historical.strategy_f5_bearish_pe_late_entry_june_holdout_protocol import (
    CANDIDATE_NAME,
    ENTRY_TIME_REGIME,
    FROZEN_CUTOFF,
    GUARDRAILS,
    PROTOCOL_VERSION,
    ROLE,
    STRATEGY_ID,
    VALIDATION_GATE,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_HOLDOUT_BACKTEST_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vote(value: float, epsilon: float = 1e-12) -> str:
    if value > epsilon:
        return "BULLISH"
    if value < -epsilon:
        return "BEARISH"
    return "FLAT"


def _entry_time_regime(
    rows: list[dict[str, Any]],
    decision_timestamp: str,
) -> dict[str, Any]:
    decision = datetime.fromisoformat(decision_timestamp)
    day_rows = sorted(
        [
            row
            for row in rows
            if datetime.fromisoformat(str(row["timestamp"])).date()
            == decision.date()
        ],
        key=lambda row: str(row["timestamp"]),
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
        return {"available": False, "regime": None, "reason": "MISSING_0915_OPEN"}

    interval = timedelta(minutes=int(WINDOW["spot_interval_minutes"]))
    completed = [
        row
        for row in day_rows
        if datetime.fromisoformat(str(row["timestamp"])) + interval <= decision
    ]
    if len(completed) < int(ENTRY_TIME_REGIME["minimum_completed_5m_bars"]):
        return {
            "available": False,
            "regime": None,
            "reason": "INSUFFICIENT_COMPLETED_5M_BARS",
            "completed_5m_bars": len(completed),
        }

    open_price = float(opening["open"])
    closes = np.asarray([float(row["close"]) for row in completed], dtype=float)
    last_close = float(closes[-1])
    median_close = float(np.median(closes))
    x = np.arange(len(closes), dtype=float)
    slope = float(np.polyfit(x, closes, 1)[0])
    values = {
        "net": 100.0 * (last_close / open_price - 1.0),
        "median": 100.0 * (median_close / open_price - 1.0),
        "slope": 100.0 * slope / open_price,
    }
    votes = {
        "net_return_vote": _vote(values["net"]),
        "median_location_vote": _vote(values["median"]),
        "session_slope_vote": _vote(values["slope"]),
    }
    if all(v == "BEARISH" for v in votes.values()):
        regime = "BEARISH"
    elif all(v == "BULLISH" for v in votes.values()):
        regime = "BULLISH"
    else:
        regime = "MIXED"
    return {
        "available": True,
        "regime": regime,
        "completed_5m_bars": len(completed),
        "votes": votes,
    }


def _spot_by_day(
    spot_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in spot_rows:
        result[str(row["date"])].append(row)
    return result


def _candidate_observations(
    obs_by_day: dict[str, dict[str, dict[str, Any]]],
    spot_by_day: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, Any]]]:
    result = copy.deepcopy(obs_by_day)
    cutoff = time.fromisoformat(FROZEN_CUTOFF)
    suppressed: list[dict[str, Any]] = []

    for day, by_ts in result.items():
        for ts, same in by_ts.items():
            pe = same.get("PE")
            if pe is None or not bool(pe.get("bullish_cross")):
                continue
            decision = datetime.fromisoformat(ts)
            if decision.time() < cutoff:
                continue
            context = _entry_time_regime(spot_by_day.get(day, []), ts)
            if context.get("regime") != "BEARISH":
                continue
            pe["bullish_cross"] = False
            suppressed.append({
                "date": day,
                "decision_timestamp": ts,
                "expiry": str(pe["expiry"]),
                "strike": int(pe["strike"]),
                "right": "PE",
                "entry_time_regime": context,
            })
    return result, suppressed


def _entry_key(trade: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(trade["entry_timestamp"]),
        str(trade["expiry"]),
        int(trade["strike"]),
        str(trade["right"]),
    )


def _target_rows(
    trades: list[dict[str, Any]],
    spot_by_day: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cutoff = time.fromisoformat(FROZEN_CUTOFF)
    for trade in trades:
        if str(trade["right"]) != "PE":
            continue
        context = _entry_time_regime(
            spot_by_day.get(str(trade["date"]), []),
            str(trade["entry_timestamp"]),
        )
        if context.get("regime") != "BEARISH":
            continue
        entry_dt = datetime.fromisoformat(str(trade["entry_timestamp"]))
        rows.append({
            "entry_key": _entry_key(trade),
            "date": str(trade["date"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "late": entry_dt.time() >= cutoff,
            "winner": float(trade["primary_cost_model"]["net_pnl_inr"]) > 0.0,
            "trail_activated": bool(trade.get("trail_activated")),
            "net_pnl_inr": float(trade["primary_cost_model"]["net_pnl_inr"]),
        })
    return rows


def _simple_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["net_pnl_inr"]) for row in rows]
    if not rows:
        return {
            "trades": 0,
            "wins": 0,
            "trail_activations": 0,
            "net_pnl_inr": 0.0,
            "average_net_pnl_inr": None,
        }
    return {
        "trades": len(rows),
        "wins": sum(bool(row["winner"]) for row in rows),
        "trail_activations": sum(bool(row["trail_activated"]) for row in rows),
        "net_pnl_inr": round(sum(values), 2),
        "average_net_pnl_inr": round(sum(values) / len(values), 2),
    }


def _gate(
    *,
    selected_sessions: int,
    baseline_target: list[dict[str, Any]],
    candidate_target: list[dict[str, Any]],
    baseline_report: dict[str, Any],
    candidate_report: dict[str, Any],
) -> dict[str, Any]:
    late = [row for row in baseline_target if row["late"]]
    candidate_keys = {tuple(row["entry_key"]) for row in candidate_target}
    baseline_winners = [row for row in baseline_target if row["winner"]]
    baseline_activated = [
        row for row in baseline_target if row["trail_activated"]
    ]
    captured_winners = sum(
        tuple(row["entry_key"]) in candidate_keys for row in baseline_winners
    )
    captured_activated = sum(
        tuple(row["entry_key"]) in candidate_keys for row in baseline_activated
    )
    winner_capture = (
        captured_winners / len(baseline_winners) * 100.0
        if baseline_winners else 100.0
    )
    activation_capture = (
        captured_activated / len(baseline_activated) * 100.0
        if baseline_activated else 100.0
    )

    coverage = {
        "selected_sessions": (
            selected_sessions >= int(VALIDATION_GATE["minimum_selected_sessions"])
        ),
        "baseline_target_trades": (
            len(baseline_target)
            >= int(
                VALIDATION_GATE[
                    "minimum_baseline_entry_time_bearish_PE_trades"
                ]
            )
        ),
        "baseline_late_target_trades": (
            len(late)
            >= int(
                VALIDATION_GATE[
                    "minimum_baseline_late_entry_time_bearish_PE_trades"
                ]
            )
        ),
    }
    if not all(coverage.values()):
        return {
            "passed": False,
            "status": "INCONCLUSIVE_COVERAGE",
            "coverage": coverage,
            "effect_checks": None,
            "baseline_target_winner_capture_pct": round(winner_capture, 2),
            "baseline_target_activation_capture_pct": round(activation_capture, 2),
            "contract": VALIDATION_GATE,
        }

    b0 = baseline_report["pooled"]["0.00"]
    c0 = candidate_report["pooled"]["0.00"]
    b05 = baseline_report["pooled"]["0.50"]
    c05 = candidate_report["pooled"]["0.50"]
    b10 = baseline_report["pooled"]["1.00"]
    c10 = candidate_report["pooled"]["1.00"]
    effect = {
        "late_baseline_subset_net_negative": (
            float(_simple_summary(late)["net_pnl_inr"]) < 0.0
        ),
        "winner_capture_ge_95": (
            winner_capture
            >= float(
                VALIDATION_GATE["minimum_baseline_target_winner_capture_pct"]
            )
        ),
        "activation_capture_ge_95": (
            activation_capture
            >= float(
                VALIDATION_GATE[
                    "minimum_baseline_target_activation_capture_pct"
                ]
            )
        ),
        "full_path_net_above_baseline_0": (
            float(c0["net_pnl_inr"]) > float(b0["net_pnl_inr"])
        ),
        "full_path_net_above_baseline_0_5": (
            float(c05["net_pnl_inr"]) > float(b05["net_pnl_inr"])
        ),
        "full_path_net_above_baseline_1": (
            float(c10["net_pnl_inr"]) > float(b10["net_pnl_inr"])
        ),
        "full_path_max_drawdown_below_baseline_0": (
            float(c0["max_drawdown_inr"]) < float(b0["max_drawdown_inr"])
        ),
    }
    return {
        "passed": all(effect.values()),
        "status": (
            "PASS_FRESH_HOLDOUT"
            if all(effect.values())
            else "FAIL_FRESH_HOLDOUT"
        ),
        "coverage": coverage,
        "effect_checks": effect,
        "baseline_target_winner_capture_pct": round(winner_capture, 2),
        "baseline_target_activation_capture_pct": round(activation_capture, 2),
        "contract": VALIDATION_GATE,
    }


def _report(trades: list[dict[str, Any]], skips: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "quality": {
            "scorable_trades": len(trades),
            "skipped_trade_intents": len(skips),
        },
        "pooled": {
            key: _summary(trades, key)
            for key in ("0.00", "0.50", "1.00")
        },
        "ce_0_00": _summary(
            [t for t in trades if str(t["right"]) == "CE"], "0.00"
        ),
        "pe_0_00": _summary(
            [t for t in trades if str(t["right"]) == "PE"], "0.00"
        ),
        "trades": trades,
        "skipped": skips,
    }


def backtest(
    payload: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("June late-entry holdout market protocol mismatch")
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("June holdout market must be outcome-unscored")

    contracts = list(payload.get("daily_contracts") or [])
    spot_rows = list(payload.get("spot_rows") or [])
    rows_1m = list(payload.get("option_rows_1m") or [])
    bars_2m, incomplete = _aggregate_2m(rows_1m)
    obs, insufficient = _observations(contracts, bars_2m)
    spot_lookup = _spot_by_day(spot_rows)
    candidate_obs, suppressed_signals = _candidate_observations(
        obs, spot_lookup
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

    baseline_report = _report(baseline_trades, baseline_skips)
    candidate_report = _report(candidate_trades, candidate_skips)
    baseline_target = _target_rows(baseline_trades, spot_lookup)
    candidate_target = _target_rows(candidate_trades, spot_lookup)
    late_baseline = [row for row in baseline_target if row["late"]]

    gate = _gate(
        selected_sessions=len(contracts),
        baseline_target=baseline_target,
        candidate_target=candidate_target,
        baseline_report=baseline_report,
        candidate_report=candidate_report,
    )

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "candidate_name": CANDIDATE_NAME,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "quality": {
            "selected_sessions": len(contracts),
            "raw_1m_option_rows": len(rows_1m),
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
            "suppressed_signal_intents": len(suppressed_signals),
        },
        "baseline": baseline_report,
        "candidate": candidate_report,
        "matched_entry_time_bearish_PE": {
            "baseline_all": _simple_summary(baseline_target),
            "baseline_late_ge_1430": _simple_summary(late_baseline),
            "baseline_before_1430": _simple_summary(
                [row for row in baseline_target if not row["late"]]
            ),
            "candidate_all": _simple_summary(candidate_target),
        },
        "suppressed_signal_sample": suppressed_signals[:100],
        "validation_gate": gate,
        "decision": (
            "F5_BEARISH_PE_1430_JUNE_HOLDOUT_PASSED_REQUIRES_SECOND_VALIDATION"
            if gate["passed"]
            else (
                "F5_BEARISH_PE_1430_JUNE_HOLDOUT_INCONCLUSIVE"
                if gate["status"] == "INCONCLUSIVE_COVERAGE"
                else "F5_BEARISH_PE_1430_JUNE_HOLDOUT_FAILED_DO_NOT_RETUNE"
            )
        ),
        "insufficient_warmup": insufficient,
        "guardrails": GUARDRAILS,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest frozen 14:30 bearish-PE late-entry rule on June"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_bearish_pe_late_entry_june_holdout_2026_06.json"
        ),
    )
    args = parser.parse_args()

    source_sha = _sha256(args.market)
    report = backtest(
        json.loads(args.market.read_text(encoding="utf-8")),
        source_sha256=source_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "sha256": _sha256(args.output),
        "source_market_sha256": source_sha,
        "quality": report["quality"],
        "baseline_pooled": report["baseline"]["pooled"],
        "candidate_pooled": report["candidate"]["pooled"],
        "matched_entry_time_bearish_PE": report[
            "matched_entry_time_bearish_PE"
        ],
        "validation_gate": report["validation_gate"],
        "decision": report["decision"],
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
