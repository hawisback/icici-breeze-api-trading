"""One-off full replay experiment with a PUT-only pullback-depth rule.

The production Strategy A implementation is not modified.  This command
temporarily substitutes a subclass in the current Python process only, then
uses the existing raw-candle SimulationEngine and PositionManager replay.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from libs.config.settings import get_platform_settings
from services.historical.repository import HistoricalRepository
from services.historical.service import HistoricalService
from services.instrument.repository import InstrumentRepository
from services.instrument.service import InstrumentService
from services.strategy.extended_replay_report import (
    _chronological_splits,
    _drawdown,
    _records,
    _stats,
)
from services.strategy.models import (
    HistoricalReplaySource,
    ThresholdOverrides,
    TradeDirection,
    SimulationRequest,
)
from services.strategy.replay_lifecycle import build_lifecycle_report
from services.strategy.replay_manifest import ReplayManifestRecord
from services.strategy.simulation import SimulationEngine
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy


class PutDepthExperimentalTrendPullbackStrategy(TrendPullbackStrategy):
    """Temporary Strategy A variant: change only PUT depth acceptance."""

    EXPERIMENT_MIN_PULLBACK_DEPTH = 0.40
    EXPERIMENT_MAX_PULLBACK_DEPTH = 0.599999

    def _reset_put_setup(self) -> None:
        state = self.state[TradeDirection.BEARISH.value]
        state.pop("impulse", None)
        state.pop("active_setup_key", None)
        state["confirm_count"] = 0

    def _decision(self, direction, features, bars, macro, futures, overrides, *args, **kwargs):
        if direction != TradeDirection.BEARISH:
            return super()._decision(direction, features, bars, macro, futures, overrides, *args, **kwargs)

        experimental_overrides = (overrides or ThresholdOverrides()).model_copy(update={
            "min_pullback_depth": self.EXPERIMENT_MIN_PULLBACK_DEPTH,
            # The production implementation uses a small comparison tolerance
            # around its inclusive max.  The post-check below enforces the
            # requested strict < 0.60 boundary for the exact signal value.
            "max_pullback_depth": self.EXPERIMENT_MAX_PULLBACK_DEPTH,
        })
        signal, diagnostic = super()._decision(
            direction, features, bars, macro, futures, experimental_overrides, *args, **kwargs
        )
        exact_depth = None
        if signal is not None:
            exact_depth = signal.features_snapshot.get("pullback_depth")
        elif diagnostic is not None:
            depth_pct = (diagnostic.phase_summary.get("pullback") or {}).get("depth_pct")
            if depth_pct is not None:
                exact_depth = float(depth_pct) / 100.0
        if exact_depth is not None and float(exact_depth) >= 0.60:
            self._reset_put_setup()
            return None, diagnostic
        return signal, diagnostic


def _month_keys() -> list[str]:
    return [*(f"2025-{m:02d}" for m in range(1, 13)), *(f"2026-{m:02d}" for m in range(1, 10))]


def _quarter(row: ReplayManifestRecord) -> str:
    month = int(row.trading_date[5:7])
    return f"{row.trading_date[:4]}-Q{(month - 1) // 3 + 1}"


def _by_month(records: list[ReplayManifestRecord], month: str) -> list[ReplayManifestRecord]:
    return [row for row in records if row.trading_date.startswith(month)]


def _monthly_comparison(
    baseline: list[ReplayManifestRecord], experimental: list[ReplayManifestRecord]
) -> dict[str, Any]:
    result = {}
    for month in _month_keys():
        before = _stats(_by_month(baseline, month))
        after = _stats(_by_month(experimental, month))
        result[month] = {
            "baseline": before,
            "experimental": after,
            "delta": {
                "trades": after["trades"] - before["trades"],
                "average_r": round(after["average_r"] - before["average_r"], 4),
                "profit_factor": round(after["profit_factor"] - before["profit_factor"], 4),
                "cumulative_r": round(after["cumulative_r"] - before["cumulative_r"], 4),
                "win_rate_pct": round(after["win_rate_pct"] - before["win_rate_pct"], 2),
            },
        }
    return result


def _quarterly(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    keys = [f"2025-Q{q}" for q in range(1, 5)] + [f"2026-Q{q}" for q in range(1, 4)]
    result = {}
    for key in keys:
        stats = _stats([row for row in records if _quarter(row) == key])
        result[key] = {field: stats[field] for field in ("trades", "average_r", "profit_factor", "cumulative_r")}
    return result


def _development_validation(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    return {
        "development_2025-01-01_to_2025-12-31": _stats([row for row in records if row.trading_date <= "2025-12-31"]),
        "validation_2026-01-01_to_2026-09-18": _stats([row for row in records if row.trading_date >= "2026-01-01"]),
    }


def _exit_analysis(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    groups: dict[str, list[ReplayManifestRecord]] = defaultdict(list)
    for row in records:
        if row.lifecycle_status == "RESOLVED" and row.realized_r is not None:
            groups[row.exit_reason or "UNKNOWN"].append(row)
    expected = {
        "STRUCTURAL_STOP",
        "THESIS_INVALIDATION",
        "ADVERSE_HEALTH_EXIT",
        "PROTECTED_BREAKEVEN",
        "TRAILING_STOP_EXIT",
        "RUNNER_STOP_EXIT",
        "SESSION_EXIT",
    }
    return {
        reason: {
            "count": len(groups.get(reason, [])),
            "average_r": round(sum(float(row.realized_r or 0.0) for row in groups.get(reason, [])) / len(groups[reason]), 4)
            if groups.get(reason) else 0.0,
        }
        for reason in sorted(expected)
    }


def _interaction(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    report = build_lifecycle_report(records, {"variant": "PUT_DEPTH_40_TO_60_FULL_REPLAY"})
    wanted = {"confirmation_count", "impulse_atr", "structural_r", "adx", "time_of_day"}
    fields = ("trades", "average_r", "profit_factor", "win_rate_pct", "max_consecutive_losses")
    output = {}
    for dimension, buckets in report["segments"].items():
        if dimension not in wanted:
            continue
        output[dimension] = {}
        for bucket, value in buckets.items():
            output[dimension][bucket] = {
                **{field: value.get(field, 0.0) for field in fields},
                "cumulative_r": round(float(value.get("average_r", 0.0)) * int(value.get("trades", 0)), 4),
            }
    return output


def _entry_comparison(
    baseline: list[ReplayManifestRecord], experimental: list[ReplayManifestRecord]
) -> dict[str, Any]:
    baseline_by_id = {row.replay_signal_id: row for row in baseline}
    experimental_by_id = {row.replay_signal_id: row for row in experimental}
    baseline_ids = set(baseline_by_id)
    experimental_ids = set(experimental_by_id)
    retained = sorted(baseline_ids & experimental_ids)
    removed = sorted(baseline_ids - experimental_ids)
    new = sorted(experimental_ids - baseline_ids)
    altered = []
    for signal_id in retained:
        before = baseline_by_id[signal_id]
        after = experimental_by_id[signal_id]
        if (
            before.simulated_entry_timestamp != after.simulated_entry_timestamp
            or float(before.simulated_entry_price) != float(after.simulated_entry_price)
        ):
            altered.append({
                "signal_id": signal_id,
                "baseline_entry_timestamp": before.simulated_entry_timestamp.isoformat(),
                "experimental_entry_timestamp": after.simulated_entry_timestamp.isoformat(),
                "baseline_entry_price": before.simulated_entry_price,
                "experimental_entry_price": after.simulated_entry_price,
            })

    # A setup-level view captures lifecycle changes that changed the generated
    # signal ID, rather than incorrectly treating every new ID as unrelated.
    baseline_by_setup = {(row.setup_id, row.direction): row for row in baseline}
    experimental_by_setup = {(row.setup_id, row.direction): row for row in experimental}
    lifecycle_changes = []
    for key in sorted(set(baseline_by_setup) & set(experimental_by_setup)):
        before = baseline_by_setup[key]
        after = experimental_by_setup[key]
        if before.replay_signal_id != after.replay_signal_id or before.simulated_entry_timestamp != after.simulated_entry_timestamp:
            lifecycle_changes.append({
                "setup_id": key[0],
                "direction": key[1],
                "baseline_signal_id": before.replay_signal_id,
                "experimental_signal_id": after.replay_signal_id,
                "baseline_entry_timestamp": before.simulated_entry_timestamp.isoformat(),
                "experimental_entry_timestamp": after.simulated_entry_timestamp.isoformat(),
            })
    return {
        "baseline_signal_ids": sorted(baseline_ids),
        "experimental_signal_ids": sorted(experimental_ids),
        "retained_signals": retained,
        "removed_signals": removed,
        "new_signals": new,
        "altered_entry_timestamps_or_prices": altered,
        "lifecycle_changed_matching_setups": lifecycle_changes,
        "counts": {
            "baseline": len(baseline_ids),
            "experimental": len(experimental_ids),
            "retained": len(retained),
            "removed": len(removed),
            "new": len(new),
            "altered_retained_entries": len(altered),
            "lifecycle_changed_matching_setups": len(lifecycle_changes),
        },
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    source = json.loads(args.baseline_report.read_text(encoding="utf-8"))
    baseline_put = _records(source["records"]["put_only"])
    settings = get_platform_settings()
    session_dates = [item["date"] for item in source["session_results"]]

    historical = HistoricalService(
        repository=HistoricalRepository(db_path=settings.historical_db_path),
        broker_gateway=None,
        instrument_service=InstrumentService(repository=InstrumentRepository(db_path=settings.instruments_db_path)),
    )
    await historical.initialize()
    await historical.instrument_service.initialize()

    import services.strategy.simulation as simulation_module
    original_strategy_class = simulation_module.TrendPullbackStrategy
    simulation_module.TrendPullbackStrategy = PutDepthExperimentalTrendPullbackStrategy
    try:
        engine = SimulationEngine(historical_service=historical)
        experimental_full: list[ReplayManifestRecord] = []
        session_metadata: list[dict[str, Any]] = []
        for day in session_dates:
            result = await engine.run_day_simulation(SimulationRequest(
                date=day,
                historical_source=HistoricalReplaySource.BREEZE,
                bypass_entry_window=False,
            ))
            records = _records(result.replay_manifests)
            experimental_full.extend(records)
            session_metadata.append({
                "date": day,
                "signals": len(records),
                "configuration_fingerprint": result.replay_metadata.get("configuration_fingerprint"),
                "dataset_hash": result.replay_metadata.get("data_fingerprint", {}).get("dataset_hash"),
            })
    finally:
        simulation_module.TrendPullbackStrategy = original_strategy_class

    experimental_full.sort(key=lambda row: row.simulated_entry_timestamp)
    experimental_put = [row for row in experimental_full if row.direction == "PUT"]
    baseline_full_records = _records(source["records"]["full_strategy_a"])
    baseline_full_stats = source["full_strategy_a"]["statistics"]
    baseline_put_stats = _stats(baseline_put)
    experimental_put_stats = _stats(experimental_put)
    experimental_put_stats["maximum_drawdown"] = _drawdown(experimental_put)
    experimental_full_stats = _stats(experimental_full)
    experimental_full_stats["maximum_drawdown"] = _drawdown(experimental_full)

    experiment_config = {
        "base_configuration_snapshot": source["metadata"]["configuration_snapshot"],
        "experiment_variant": "PUT_PULLBACK_DEPTH_40_INCLUSIVE_TO_60_EXCLUSIVE",
        "call_pullback_depth": {"min_inclusive": 0.08, "max_inclusive": 0.70},
        "put_pullback_depth": {"min_inclusive": 0.40, "max_exclusive": 0.60},
        "strategy_a_call_entries_disabled": False,
        "historical_source": "BREEZE",
        "bypass_entry_window": False,
        "threshold_overrides": "none_except_experimental_put_pullback_rule",
    }
    config_fingerprint = hashlib.sha256(
        json.dumps(experiment_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    data_fingerprint = source["metadata"]["data_fingerprint"]
    if data_fingerprint.get("dataset_hash") != source["metadata"]["data_fingerprint"].get("dataset_hash"):
        raise AssertionError("experiment must preserve the authoritative dataset fingerprint")

    lifecycle_report = build_lifecycle_report(experimental_full, {"sessions": len(session_dates), "variant": experiment_config["experiment_variant"]})
    baseline_call = [row for row in baseline_full_records if row.direction == "CALL"]
    experimental_call = [row for row in experimental_full if row.direction == "CALL"]
    call_lifecycle = _entry_comparison(baseline_call, experimental_call)
    output = {
        "experiment": {
            "date_range": {"start": "2025-01-01", "end": "2026-09-18"},
            "full_raw_candle_replay": True,
            "source": "BREEZE",
            "quality_approved_sessions": len(session_dates),
            "production_strategy_changed": False,
        },
        "experimental_configuration": experiment_config,
        "configuration_fingerprint": config_fingerprint,
        "data_fingerprint": data_fingerprint,
        "experimental_put": {
            "statistics": experimental_put_stats,
            "monthly": _monthly_comparison(baseline_put, experimental_put),
            "quarterly": _quarterly(experimental_put),
            "development_validation": _development_validation(experimental_put),
            "chronological_stability": _chronological_splits(experimental_put),
            "drawdown": experimental_put_stats["maximum_drawdown"],
            "exit_analysis": _exit_analysis(experimental_put),
            "interaction_analysis": _interaction(experimental_put),
            "weak_months": {
                month: _stats(_by_month(experimental_put, month))
                for month in ("2025-03", "2025-11", "2025-12", "2026-04", "2026-05", "2026-07")
            },
        },
        "baseline_put": {
            "statistics": baseline_put_stats,
            "reported_statistics": source["put_only"]["statistics"],
            "drawdown": source["put_only"]["maximum_drawdown"],
            "exit_analysis": _exit_analysis(baseline_put),
        },
        "comparison": {
            "signals": {"baseline": baseline_put_stats["signals"], "experimental": experimental_put_stats["signals"], "delta": experimental_put_stats["signals"] - baseline_put_stats["signals"]},
            "resolved": {"baseline": baseline_put_stats["resolved"], "experimental": experimental_put_stats["resolved"], "delta": experimental_put_stats["resolved"] - baseline_put_stats["resolved"]},
            "ambiguous": {"baseline": baseline_put_stats["ambiguous"], "experimental": experimental_put_stats["ambiguous"], "delta": experimental_put_stats["ambiguous"] - baseline_put_stats["ambiguous"]},
            "winners": {"baseline": baseline_put_stats["winners"], "experimental": experimental_put_stats["winners"], "delta": experimental_put_stats["winners"] - baseline_put_stats["winners"]},
            "losers": {"baseline": baseline_put_stats["losers"], "experimental": experimental_put_stats["losers"], "delta": experimental_put_stats["losers"] - baseline_put_stats["losers"]},
            "win_rate_pct": {"baseline": baseline_put_stats["win_rate_pct"], "experimental": experimental_put_stats["win_rate_pct"], "delta": round(experimental_put_stats["win_rate_pct"] - baseline_put_stats["win_rate_pct"], 2)},
            "average_winner_r": {"baseline": baseline_put_stats["average_winner_r"], "experimental": experimental_put_stats["average_winner_r"], "delta": round(experimental_put_stats["average_winner_r"] - baseline_put_stats["average_winner_r"], 4)},
            "average_loser_r": {"baseline": baseline_put_stats["average_loser_r"], "experimental": experimental_put_stats["average_loser_r"], "delta": round(experimental_put_stats["average_loser_r"] - baseline_put_stats["average_loser_r"], 4)},
            "average_r": {"baseline": baseline_put_stats["average_r"], "experimental": experimental_put_stats["average_r"], "delta": round(experimental_put_stats["average_r"] - baseline_put_stats["average_r"], 4)},
            "median_r": {"baseline": baseline_put_stats["median_r"], "experimental": experimental_put_stats["median_r"], "delta": round(experimental_put_stats["median_r"] - baseline_put_stats["median_r"], 4)},
            "profit_factor": {"baseline": baseline_put_stats["profit_factor"], "experimental": experimental_put_stats["profit_factor"], "delta": round(experimental_put_stats["profit_factor"] - baseline_put_stats["profit_factor"], 4)},
            "cumulative_r": {"baseline": baseline_put_stats["cumulative_r"], "experimental": experimental_put_stats["cumulative_r"], "delta": round(experimental_put_stats["cumulative_r"] - baseline_put_stats["cumulative_r"], 4)},
            "max_consecutive_losses": {"baseline": baseline_put_stats["max_consecutive_losses"], "experimental": experimental_put_stats["max_consecutive_losses"], "delta": experimental_put_stats["max_consecutive_losses"] - baseline_put_stats["max_consecutive_losses"]},
            "maximum_drawdown_r": {"baseline": source["put_only"]["maximum_drawdown"]["maximum_peak_to_trough_drawdown_r"], "experimental": experimental_put_stats["maximum_drawdown"]["maximum_peak_to_trough_drawdown_r"], "delta": round(experimental_put_stats["maximum_drawdown"]["maximum_peak_to_trough_drawdown_r"] - source["put_only"]["maximum_drawdown"]["maximum_peak_to_trough_drawdown_r"], 4)},
        },
        "signal_lifecycle_comparison": _entry_comparison(baseline_put, experimental_put),
        "full_strategy_effect": {
            "experimental_full_call_plus_put": experimental_full_stats,
            "call_unchanged_check": call_lifecycle,
            "lifecycle_report": lifecycle_report,
        },
        "session_metadata": session_metadata,
        "replay_results": {
            "experimental_full_records": [row.model_dump(mode="json") for row in experimental_full],
            "experimental_put_records": [row.model_dump(mode="json") for row in experimental_put],
        },
        "signal_ids": {
            "baseline_put": [row.replay_signal_id for row in baseline_put],
            "experimental_full": [row.replay_signal_id for row in experimental_full],
            "experimental_put": [row.replay_signal_id for row in experimental_put],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run controlled PUT pullback-depth full replay")
    parser.add_argument("--baseline-report", type=Path, default=Path("data/extended_strategy_a_replay_report.json"))
    parser.add_argument("--output", type=Path, default=Path("data/put_depth_full_replay_experiment.json"))
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps({
        "output": str(args.output),
        "experimental_put": result["experimental_put"]["statistics"],
        "full_strategy": result["full_strategy_effect"]["experimental_full_call_plus_put"],
        "lifecycle_counts": result["signal_lifecycle_comparison"]["counts"],
        "configuration_fingerprint": result["configuration_fingerprint"],
        "dataset_hash": result["data_fingerprint"]["dataset_hash"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
