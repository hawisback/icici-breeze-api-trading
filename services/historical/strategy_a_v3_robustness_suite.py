"""Comprehensive read-only robustness suite for frozen Strategy A V3.

The suite deliberately separates robustness from optimization:

1. Replays the exact production state machine over every cached Breeze session.
2. Audits the production rule funnel to identify where opportunity is lost.
3. Re-runs the corrected V3 momentum-context validation and nearby guards.
4. Replays every actual production signal through the corrected lifecycle path.
5. Reports cadence, chronological/directional breakdowns, concentration,
   bootstrap uncertainty, and generic R-haircut headroom.

No production threshold is changed.  Historical option fills are not invented.
The lifecycle metrics remain underlying-futures R, not executable option P&L.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable

from services.historical.strategy_a_data_audit import IST, _default_db_path, _open_read_only
from services.historical.strategy_a_research import _session_dates
from services.historical.strategy_a_rule_funnel_audit import audit_rule_funnel
from services.historical.strategy_a_state_machine_audit import audit_state_machine
from services.historical.strategy_a_v3_lifecycle_batch import _aggregate, run_batch
from services.historical.strategy_a_v3_momentum_validation import build_validation
from services.strategy.models import HistoricalReplaySource


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _discover_dates(db_path: Path, *, source: str) -> list[str]:
    conn = _open_read_only(db_path)
    try:
        return [
            day.isoformat()
            for day in _session_dates(conn, sessions=0, source=source)
        ]
    finally:
        conn.close()


def _resolved_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in records
        if row.get("lifecycle_status") == "RESOLVED"
        and row.get("realized_r") is not None
    ]


def _entry_ist(record: dict[str, Any]) -> datetime:
    raw = str(record.get("simulated_entry_timestamp") or "")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(IST)


def _time_bucket(record: dict[str, Any]) -> str:
    local = _entry_ist(record)
    minute = local.hour * 60 + local.minute
    if minute < 10 * 60 + 30:
        return "09:45-10:29"
    if minute < 11 * 60 + 30:
        return "10:30-11:29"
    if minute < 12 * 60 + 30:
        return "11:30-12:29"
    if minute < 13 * 60 + 30:
        return "12:30-13:29"
    return "13:30-14:45"


def _group_aggregate(
    records: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        groups[key_fn(row)].append(row)
    return {
        key: _aggregate(group)
        for key, group in sorted(groups.items())
    }


def _bootstrap_mean_ci(
    records: list[dict[str, Any]],
    *,
    iterations: int = 5000,
    seed: int = 20260922,
) -> dict[str, Any]:
    values = [float(row["realized_r"]) for row in _resolved_records(records)]
    if not values:
        return {
            "sample_size": 0,
            "iterations": iterations,
            "mean_r": None,
            "ci95_low_r": None,
            "ci95_high_r": None,
        }
    rng = random.Random(seed)
    samples: list[float] = []
    count = len(values)
    for _ in range(iterations):
        samples.append(mean(values[rng.randrange(count)] for _ in range(count)))
    samples.sort()
    low_index = max(0, int(0.025 * (iterations - 1)))
    high_index = min(iterations - 1, int(0.975 * (iterations - 1)))
    return {
        "sample_size": count,
        "iterations": iterations,
        "seed": seed,
        "mean_r": round(mean(values), 6),
        "ci95_low_r": round(samples[low_index], 6),
        "ci95_high_r": round(samples[high_index], 6),
        "note": (
            "Non-parametric bootstrap over the small observed production-lifecycle "
            "trade sample. It quantifies sampling uncertainty; it is not a forecast."
        ),
    }


def _concentration(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["realized_r"]) for row in _resolved_records(records)]
    ordered = sorted(values, reverse=True)
    total = sum(values)
    best = ordered[0] if ordered else None
    worst = min(values) if values else None
    top_two = sum(ordered[:2]) if ordered else 0.0
    return {
        "total_r": round(total, 6),
        "best_trade_r": round(best, 6) if best is not None else None,
        "worst_trade_r": round(worst, 6) if worst is not None else None,
        "best_trade_share_of_total_pct": (
            round(best / total * 100, 2)
            if best is not None and total > 0
            else None
        ),
        "top_two_share_of_total_pct": (
            round(top_two / total * 100, 2)
            if ordered and total > 0
            else None
        ),
        "total_without_best_trade_r": (
            round(total - best, 6) if best is not None else None
        ),
        "total_without_best_two_trades_r": round(total - top_two, 6),
    }


def _r_haircut_stress(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["realized_r"]) for row in _resolved_records(records)]
    result: dict[str, Any] = {}
    for haircut in (0.10, 0.25, 0.50):
        stressed = [value - haircut for value in values]
        result[f"{haircut:.2f}R_per_trade"] = {
            "sum_r": round(sum(stressed), 6),
            "mean_r": round(mean(stressed), 6) if stressed else None,
            "positive_trades": sum(value > 0 for value in stressed),
            "nonpositive_trades": sum(value <= 0 for value in stressed),
        }
    return {
        "generic_haircuts": result,
        "note": (
            "These are generic per-trade R haircuts, not modeled option slippage "
            "or transaction costs. They measure underlying-edge headroom only."
        ),
    }


def _cadence(state_report: dict[str, Any]) -> dict[str, Any]:
    sessions = sorted(
        state_report.get("sessions") or [],
        key=lambda row: str(row.get("date")),
    )
    usable = [row for row in sessions if not row.get("skip_reason")]
    usable_dates = [str(row["date"]) for row in usable]
    index = {day: i for i, day in enumerate(usable_dates)}
    setup_dates = sorted({
        str(row["date"]) for row in usable
        if int(row.get("setup_count", 0)) > 0
    })
    signal_dates = sorted({
        str(row["date"]) for row in usable
        if int(row.get("signal_count", 0)) > 0
    })
    gaps = [
        index[right] - index[left]
        for left, right in zip(signal_dates, signal_dates[1:])
        if left in index and right in index
    ]
    return {
        "sessions_total": len(sessions),
        "usable_sessions": len(usable),
        "skipped_sessions": len(sessions) - len(usable),
        "setup_days": len(setup_dates),
        "setup_day_pct": (
            round(len(setup_dates) / len(usable) * 100, 2) if usable else 0.0
        ),
        "signal_days": len(signal_dates),
        "signal_day_pct": (
            round(len(signal_dates) / len(usable) * 100, 2) if usable else 0.0
        ),
        "usable_sessions_per_signal_day": (
            round(len(usable) / len(signal_dates), 2) if signal_dates else None
        ),
        "median_usable_session_gap_between_signal_days": (
            round(float(median(gaps)), 2) if gaps else None
        ),
        "max_usable_session_gap_between_signal_days": max(gaps) if gaps else None,
        "setup_dates": setup_dates,
        "signal_dates": signal_dates,
    }


def _fold_stability(momentum_report: dict[str, Any], variant: str) -> dict[str, Any]:
    payload = (momentum_report.get("variants") or {}).get(variant) or {}
    folds = payload.get("folds") or []
    populated = [
        fold for fold in folds
        if int((fold.get("metrics") or {}).get("triggered_rows") or 0) > 0
    ]
    positive = [
        fold for fold in populated
        if float((fold.get("metrics") or {}).get("sum_t1_first_hit_r") or 0.0) > 0
    ]
    return {
        "variant": variant,
        "all_sessions": payload.get("all_sessions") or {},
        "folds_total": len(folds),
        "folds_with_triggers": len(populated),
        "positive_triggered_folds": len(positive),
        "positive_triggered_fold_pct": (
            round(len(positive) / len(populated) * 100, 2)
            if populated
            else 0.0
        ),
    }


def _momentum_plateau(momentum_report: dict[str, Any]) -> dict[str, Any]:
    names = (
        "guard_d2_m2_slope_0_015",
        "guard_d2_m25_slope_0_015",
        "guard_d2_m2_slope_0025_015",
        "guard_d2_m2_slope_0_020",
        "without_adx_floor",
        "baseline_v2",
    )
    variants = {
        name: _fold_stability(momentum_report, name)
        for name in names
        if name in (momentum_report.get("variants") or {})
    }
    core_neighbors = [
        variants[name]["all_sessions"]
        for name in (
            "guard_d2_m2_slope_0_015",
            "guard_d2_m25_slope_0_015",
            "guard_d2_m2_slope_0_020",
        )
        if name in variants
    ]
    return {
        "variants": variants,
        "core_neighbor_all_session_sums_positive": bool(core_neighbors) and all(
            float(metrics.get("sum_t1_first_hit_r") or 0.0) > 0
            for metrics in core_neighbors
        ),
        "note": (
            "Neighbor variants are robustness probes only. The suite does not "
            "promote the highest in-sample variant or change production thresholds."
        ),
    }


def _funnel_summary(funnel: dict[str, Any]) -> dict[str, Any]:
    aggregate = funnel.get("aggregate") or {}
    blockers = aggregate.get("blocker_counts") or {}
    sorted_blockers = sorted(
        blockers.items(),
        key=lambda item: int(item[1]),
        reverse=True,
    )
    return {
        "completed_15m_decision_bars": funnel.get("completed_15m_decision_bars"),
        "directional_evaluations": funnel.get("directional_evaluations"),
        "progressive_funnel": aggregate.get("progressive_funnel") or {},
        "gate_funnel": aggregate.get("gate_funnel") or {},
        "component_funnel": aggregate.get("component_funnel") or {},
        "top_blockers": [
            {"reason": reason, "count": count}
            for reason, count in sorted_blockers[:12]
        ],
        "gate_reason_counts": aggregate.get("gate_reason_counts") or {},
        "ready_by_direction": aggregate.get("ready_by_direction") or {},
        "data_quality_counts": aggregate.get("data_quality_counts") or {},
    }


def _lifecycle_analysis(lifecycle: dict[str, Any]) -> dict[str, Any]:
    records = list(lifecycle.get("records") or [])
    return {
        "aggregate": lifecycle.get("strategy_a") or {},
        "signal_parity": lifecycle.get("signal_parity") or {},
        "by_direction": _group_aggregate(
            records,
            lambda row: str(row.get("direction") or "UNKNOWN"),
        ),
        "by_year": _group_aggregate(
            records,
            lambda row: str(row.get("trading_date") or "")[:4] or "UNKNOWN",
        ),
        "by_quarter": _group_aggregate(
            records,
            lambda row: (
                f"{_entry_ist(row).year}-Q{((_entry_ist(row).month - 1) // 3) + 1}"
            ),
        ),
        "by_entry_time_bucket_ist": _group_aggregate(records, _time_bucket),
        "bootstrap_mean_r": _bootstrap_mean_ci(records),
        "concentration": _concentration(records),
        "r_haircut_stress": _r_haircut_stress(records),
    }


async def run_suite(
    db_path: Path,
    *,
    source: str,
    output_dir: Path,
) -> dict[str, Any]:
    dates = _discover_dates(db_path, source=source)
    if not dates:
        raise RuntimeError(f"no cached {source} sessions found")
    session_count = len(dates)

    state = audit_state_machine(
        db_path,
        sessions=session_count,
        source=source,
    )
    state_path = output_dir / "state_machine_all_sessions.json"
    _write_json(state_path, state)

    funnel = audit_rule_funnel(
        db_path,
        sessions=session_count,
        source=source,
    )
    funnel_path = output_dir / "rule_funnel_all_sessions.json"
    _write_json(funnel_path, funnel)

    momentum = build_validation(
        db_path,
        sessions=0,
        source=source,
    )
    momentum_path = output_dir / "momentum_validation_all_sessions.json"
    _write_json(momentum_path, momentum)

    lifecycle = await run_batch(
        state_path,
        db_path=db_path,
        source=HistoricalReplaySource(source),
    )
    lifecycle_path = output_dir / "production_lifecycle_all_signals.json"
    _write_json(lifecycle_path, lifecycle)

    report = {
        "report_type": "STRATEGY_A_V3_FROZEN_ROBUSTNESS_SUITE",
        "source": source.upper(),
        "db_path": str(db_path.resolve()),
        "cached_sessions_discovered": session_count,
        "cached_date_range": {
            "first": dates[0],
            "last": dates[-1],
        },
        "production_config_changed": False,
        "broker_called": False,
        "historical_market_data_written": False,
        "cadence": _cadence(state),
        "opportunity_funnel": _funnel_summary(funnel),
        "momentum_threshold_plateau": _momentum_plateau(momentum),
        "production_lifecycle": _lifecycle_analysis(lifecycle),
        "limitations": [
            "Momentum validation uses independent research opportunities and is not portfolio P&L.",
            "Production lifecycle metrics are authoritative futures/underlying R, not historical executable option P&L.",
            "Bootstrap intervals describe sampling uncertainty in the observed lifecycle trades and are not forward-return forecasts.",
            "Generic R haircuts are stress tests, not modeled option slippage or transaction costs.",
            "No threshold is selected or changed by this suite.",
        ],
        "outputs": {
            "state_machine": str(state_path),
            "rule_funnel": str(funnel_path),
            "momentum_validation": str(momentum_path),
            "production_lifecycle": str(lifecycle_path),
        },
    }
    report_path = output_dir / "strategy_a_v3_robustness_summary.json"
    _write_json(report_path, report)
    report["outputs"]["summary"] = str(report_path)
    _write_json(report_path, report)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the frozen Strategy A V3 all-session robustness suite"
    )
    parser.add_argument("--db-path", type=Path, default=_default_db_path())
    parser.add_argument(
        "--source",
        choices=("BREEZE", "KITE", "LIVE", "MIXED"),
        default="BREEZE",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data") / "strategy_a_v3_robustness",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = asyncio.run(
        run_suite(
            args.db_path,
            source=args.source,
            output_dir=args.output_dir,
        )
    )
    compact = {
        "report_type": report["report_type"],
        "cached_sessions_discovered": report["cached_sessions_discovered"],
        "cadence": report["cadence"],
        "momentum_threshold_plateau": report["momentum_threshold_plateau"],
        "production_lifecycle": report["production_lifecycle"],
        "outputs": report["outputs"],
    }
    print(json.dumps(compact, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
