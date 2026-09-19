"""Frozen-signal pullback-depth screening for the authoritative PUT manifests.

This module reads an existing extended replay artifact.  It never calls the
strategy, changes thresholds, or reruns lifecycle resolution.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from services.strategy.extended_replay_report import (
    _drawdown,
    _records,
    _stats,
)
from services.strategy.replay_lifecycle import build_lifecycle_report
from services.strategy.replay_manifest import ReplayManifestRecord


def _month_keys() -> list[str]:
    return [
        *(f"2025-{month:02d}" for month in range(1, 13)),
        *(f"2026-{month:02d}" for month in range(1, 10)),
    ]


def _quarter_keys() -> list[str]:
    return [
        "2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4",
        "2026-Q1", "2026-Q2", "2026-Q3",
    ]


def _group(records: list[ReplayManifestRecord], key: str) -> list[ReplayManifestRecord]:
    return [row for row in records if row.trading_date.startswith(key)]


def _quarter(row: ReplayManifestRecord) -> str:
    month = int(row.trading_date[5:7])
    return f"{row.trading_date[:4]}-Q{(month - 1) // 3 + 1}"


def _empty_stats() -> dict[str, Any]:
    return _stats([])


def _monthly(records: list[ReplayManifestRecord]) -> tuple[dict[str, Any], dict[str, Any]]:
    result: dict[str, Any] = {}
    for key in _month_keys():
        result[key] = _stats(_group(records, key))
    active = [value for value in result.values() if value["trades"] > 0]
    profitable = sum(1 for value in active if value["cumulative_r"] > 0)
    losing = sum(1 for value in active if value["cumulative_r"] < 0)
    return result, {
        "months_in_range": len(result),
        "months_with_trades": len(active),
        "profitable_months": profitable,
        "losing_months": losing,
        "zero_trade_months": len(result) - len(active),
        "percentage_profitable_months_of_months_with_trades": round(profitable / len(active) * 100, 2) if active else 0.0,
    }


def _quarterly(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in _quarter_keys():
        stats = _stats([row for row in records if _quarter(row) == key])
        result[key] = {
            field: stats[field]
            for field in ("trades", "average_r", "profit_factor", "cumulative_r")
        }
    return result


def _chronological(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda row: row.simulated_entry_timestamp)
    if not ordered:
        return {key: _stats([]) for key in ("first_50_pct", "second_50_pct", "first_third", "second_third", "final_third")}
    n = len(ordered)
    half = (n + 1) // 2
    first = (n + 2) // 3
    second = (2 * n + 2) // 3
    slices = {
        "first_50_pct": ordered[:half],
        "second_50_pct": ordered[half:],
        "first_third": ordered[:first],
        "second_third": ordered[first:second],
        "final_third": ordered[second:],
    }
    result: dict[str, Any] = {}
    for key, subset in slices.items():
        stats = _stats(subset)
        result[key] = {
            field: stats[field]
            for field in ("trades", "average_r", "profit_factor", "cumulative_r", "max_consecutive_losses")
        }
        result[key]["signals"] = len(subset)
        result[key]["date_range"] = {
            "start": subset[0].trading_date if subset else None,
            "end": subset[-1].trading_date if subset else None,
        }
    return result


def _interaction(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    report = build_lifecycle_report(records, {"screen": "40_TO_60_PULLBACK_DEPTH"})
    fields = ("trades", "average_r", "profit_factor", "cumulative_r", "win_rate_pct", "max_consecutive_losses")
    return {
        dimension: {
            bucket: {
                **{field: value.get(field, 0.0) for field in fields},
                "cumulative_r": round(float(value.get("average_r", 0.0)) * int(value.get("trades", 0)), 4),
            }
            for bucket, value in buckets.items()
        }
        for dimension, buckets in report["segments"].items()
        if dimension in {"confirmation_count", "impulse_atr", "structural_r", "adx", "time_of_day"}
    }


def _screened_records(records: list[ReplayManifestRecord], low: float, high: float) -> list[ReplayManifestRecord]:
    return [
        row for row in records
        if row.entry_features.get("pullback_depth") is not None
        and low <= float(row.entry_features["pullback_depth"]) < high
    ]


def run(input_path: Path, output_path: Path) -> dict[str, Any]:
    source = json.loads(input_path.read_text(encoding="utf-8"))
    baseline_records = _records(source["records"]["put_only"])
    baseline = _stats(baseline_records)
    subset = _screened_records(baseline_records, 0.40, 0.60)
    subset_stats = _stats(subset)
    subset_stats["maximum_drawdown"] = _drawdown(subset)
    months, month_summary = _monthly(subset)
    weak_months = {
        month: months[month]
        for month in ("2025-03", "2025-11", "2025-12", "2026-04", "2026-05", "2026-07")
    }

    neighboring_ranges = {}
    for label, low, high in (
        ("30-60%", 0.30, 0.60),
        ("35-60%", 0.35, 0.60),
        ("40-60%", 0.40, 0.60),
        ("40-65%", 0.40, 0.65),
        ("35-65%", 0.35, 0.65),
    ):
        screened = _screened_records(baseline_records, low, high)
        stats = _stats(screened)
        stats["maximum_drawdown_r"] = _drawdown(screened)["maximum_peak_to_trough_drawdown_r"]
        stats["range"] = {"min_inclusive": low, "max_exclusive": high}
        neighboring_ranges[label] = {
            field: stats[field]
            for field in ("trades", "average_r", "profit_factor", "cumulative_r", "maximum_drawdown_r")
        } | {"range": stats["range"]}

    development = [row for row in subset if row.trading_date <= "2025-12-31"]
    validation = [row for row in subset if row.trading_date >= "2026-01-01"]
    output = {
        "analysis": {
            "type": "FROZEN_SIGNAL_SCREEN",
            "source_report": str(input_path),
            "range": {"min_inclusive": 0.40, "max_exclusive": 0.60},
            "strategy_changes": False,
            "threshold_changes": False,
            "baseline_signal_ids_unchanged": True,
        },
        "authoritative_metadata": source["metadata"],
        "baseline_put": baseline,
        "subset_40_to_60": {
            **subset_stats,
            "percentage_of_existing_put_signals_retained": round(len(subset) / baseline["signals"] * 100, 2) if baseline["signals"] else 0.0,
            "percentage_of_existing_put_trades_retained": round(subset_stats["trades"] / baseline["trades"] * 100, 2) if baseline["trades"] else 0.0,
        },
        "monthly": months,
        "monthly_summary": month_summary,
        "quarterly": _quarterly(subset),
        "development_validation": {
            "development_2025-01-01_to_2025-12-31": _stats(development),
            "validation_2026-01-01_to_2026-09-18": _stats(validation),
        },
        "chronological_stability": _chronological(subset),
        "neighboring_depth_ranges": neighboring_ranges,
        "weak_months": weak_months,
        "interaction_checks": _interaction(subset),
        "signal_ids": [row.replay_signal_id for row in subset],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen frozen PUT manifests by pullback depth")
    parser.add_argument("--input", type=Path, default=Path("data/extended_strategy_a_replay_report.json"))
    parser.add_argument("--output", type=Path, default=Path("data/put_pullback_depth_screen.json"))
    args = parser.parse_args()
    result = run(args.input, args.output)
    subset = result["subset_40_to_60"]
    print(json.dumps({
        "output": str(args.output),
        "signals": subset["signals"],
        "trades": subset["trades"],
        "average_r": subset["average_r"],
        "profit_factor": subset["profit_factor"],
        "cumulative_r": subset["cumulative_r"],
        "maximum_drawdown_r": subset["maximum_drawdown"]["maximum_peak_to_trough_drawdown_r"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
