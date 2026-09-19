"""One-off full replay for the PUT 30%-to-60% pullback experiment.

This module temporarily substitutes a Strategy A subclass in-process.  It does
not modify the production strategy or any live-trading configuration.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import hashlib
import json
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
from services.strategy.models import HistoricalReplaySource, SimulationRequest
from services.strategy.put_depth_full_replay_experiment import (
    PutDepthExperimentalTrendPullbackStrategy,
    _development_validation,
    _entry_comparison,
    _exit_analysis,
    _month_keys,
    _quarter,
)
from services.strategy.simulation import SimulationEngine


class PutDepth30To60TrendPullbackStrategy(PutDepthExperimentalTrendPullbackStrategy):
    """Temporary PUT-only variant with 30% inclusive minimum depth."""

    EXPERIMENT_MIN_PULLBACK_DEPTH = 0.30
    EXPERIMENT_MAX_PULLBACK_DEPTH = 0.599999


def _by_month(records, month: str):
    return [row for row in records if row.trading_date.startswith(month)]


def _monthly_three(baseline, prior, experimental) -> dict[str, Any]:
    result = {}
    for month in _month_keys():
        result[month] = {
            "baseline_8_to_70": _stats(_by_month(baseline, month)),
            "experimental_40_to_60": _stats(_by_month(prior, month)),
            "experimental_30_to_60": _stats(_by_month(experimental, month)),
        }
    return result


def _quarterly_three(baseline, prior, experimental) -> dict[str, Any]:
    keys = [f"2025-Q{q}" for q in range(1, 5)] + [f"2026-Q{q}" for q in range(1, 4)]
    result = {}
    for key in keys:
        def select(records):
            return _stats([row for row in records if _quarter(row) == key])

        result[key] = {
            "baseline_8_to_70": select(baseline),
            "experimental_40_to_60": select(prior),
            "experimental_30_to_60": select(experimental),
        }
    return result


def _stats_with_drawdown(records):
    result = _stats(records)
    result["maximum_drawdown"] = _drawdown(records)
    return result


def _development_validation_with_drawdown(records):
    result = _development_validation(records)
    result["development_2025-01-01_to_2025-12-31"]["maximum_drawdown"] = _drawdown(
        [row for row in records if row.trading_date <= "2025-12-31"]
    )
    result["validation_2026-01-01_to_2026-09-18"]["maximum_drawdown"] = _drawdown(
        [row for row in records if row.trading_date >= "2026-01-01"]
    )
    return result


def _entry_change_summary(comparison: dict[str, Any]) -> dict[str, int]:
    altered = comparison["altered_entry_timestamps_or_prices"]
    return {
        **comparison["counts"],
        "altered_entry_timestamps": sum(
            row["baseline_entry_timestamp"] != row["experimental_entry_timestamp"]
            for row in altered
        ),
        "altered_entry_prices": sum(
            row["baseline_entry_price"] != row["experimental_entry_price"]
            for row in altered
        ),
    }


def _trade_frequency(experimental, baseline, prior, sessions: int) -> dict[str, float]:
    exp_trades = _stats(experimental)["trades"]
    base_signals = _stats(baseline)["signals"]
    prior_trades = _stats(prior)["trades"]
    return {
        "experimental_trades_per_session": round(exp_trades / sessions, 4),
        "experimental_signals_as_pct_of_baseline_put_signals": round(
            100.0 * len(experimental) / base_signals, 2
        ),
        "additional_trades_vs_40_to_60": exp_trades - prior_trades,
        "additional_trades_as_pct_of_40_to_60_trade_count": round(
            100.0 * (exp_trades - prior_trades) / prior_trades, 2
        ) if prior_trades else 0.0,
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    source = json.loads(args.baseline_report.read_text(encoding="utf-8"))
    prior = json.loads(args.prior_report.read_text(encoding="utf-8"))
    baseline_put = _records(source["records"]["put_only"])
    prior_put = _records(prior["replay_results"]["experimental_put_records"])
    baseline_full = _records(source["records"]["full_strategy_a"])
    prior_full = _records(prior["replay_results"]["experimental_full_records"])
    session_dates = [item["date"] for item in source["session_results"]]

    if source["metadata"]["data_fingerprint"] != prior["data_fingerprint"]:
        raise AssertionError("baseline and 40-60 experiment use different data fingerprints")

    settings = get_platform_settings()
    historical = HistoricalService(
        repository=HistoricalRepository(db_path=settings.historical_db_path),
        broker_gateway=None,
        instrument_service=InstrumentService(
            repository=InstrumentRepository(db_path=settings.instruments_db_path)
        ),
    )
    await historical.initialize()
    await historical.instrument_service.initialize()

    import services.strategy.simulation as simulation_module

    original_strategy_class = simulation_module.TrendPullbackStrategy
    simulation_module.TrendPullbackStrategy = PutDepth30To60TrendPullbackStrategy
    try:
        engine = SimulationEngine(historical_service=historical)
        experimental_full = []
        session_metadata = []
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

    experiment_config = {
        "base_configuration_snapshot": source["metadata"]["configuration_snapshot"],
        "experiment_variant": "PUT_PULLBACK_DEPTH_30_INCLUSIVE_TO_60_EXCLUSIVE",
        "call_pullback_depth": {"min_inclusive": 0.08, "max_inclusive": 0.70},
        "put_pullback_depth": {"min_inclusive": 0.30, "max_exclusive": 0.60},
        "strategy_a_call_entries_disabled": False,
        "historical_source": "BREEZE",
        "bypass_entry_window": False,
        "threshold_overrides": "none_except_experimental_put_pullback_rule",
    }
    config_fingerprint = hashlib.sha256(
        json.dumps(experiment_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    data_fingerprint = source["metadata"]["data_fingerprint"]

    baseline_stats = _stats_with_drawdown(baseline_put)
    prior_stats = _stats_with_drawdown(prior_put)
    experimental_stats = _stats_with_drawdown(experimental_put)
    baseline_full_stats = _stats_with_drawdown(baseline_full)
    prior_full_stats = _stats_with_drawdown(prior_full)
    experimental_full_stats = _stats_with_drawdown(experimental_full)

    baseline_lifecycle = _entry_comparison(baseline_put, experimental_put)
    prior_lifecycle = _entry_comparison(prior_put, experimental_put)
    baseline_call_lifecycle = _entry_comparison(
        [row for row in baseline_full if row.direction == "CALL"],
        [row for row in experimental_full if row.direction == "CALL"],
    )

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
            "statistics": experimental_stats,
            "development_validation": _development_validation_with_drawdown(experimental_put),
            "chronological_stability": _chronological_splits(experimental_put),
            "monthly": _monthly_three(baseline_put, prior_put, experimental_put),
            "quarterly": _quarterly_three(baseline_put, prior_put, experimental_put),
            "drawdown": experimental_stats["maximum_drawdown"],
            "exit_analysis": _exit_analysis(experimental_put),
        },
        "comparison_variants": {
            "baseline_8_to_70": {"statistics": baseline_stats, "exit_analysis": _exit_analysis(baseline_put)},
            "experimental_40_to_60": {"statistics": prior_stats, "exit_analysis": _exit_analysis(prior_put)},
            "experimental_30_to_60": {"statistics": experimental_stats, "exit_analysis": _exit_analysis(experimental_put)},
        },
        "signal_lifecycle_comparison": {
            "against_baseline_8_to_70": baseline_lifecycle,
            "against_experiment_40_to_60": prior_lifecycle,
            "summary_against_baseline": _entry_change_summary(baseline_lifecycle),
            "summary_against_40_to_60": _entry_change_summary(prior_lifecycle),
        },
        "trade_frequency": _trade_frequency(experimental_put, baseline_put, prior_put, len(session_dates)),
        "full_strategy_effect": {
            "baseline_current": baseline_full_stats,
            "experimental_40_to_60": prior_full_stats,
            "experimental_30_to_60": experimental_full_stats,
            "call_unchanged_check": baseline_call_lifecycle,
        },
        "session_metadata": session_metadata,
        "replay_results": {
            "experimental_full_records": [row.model_dump(mode="json") for row in experimental_full],
            "experimental_put_records": [row.model_dump(mode="json") for row in experimental_put],
        },
        "signal_ids": {
            "baseline_put": [row.replay_signal_id for row in baseline_put],
            "prior_40_to_60_put": [row.replay_signal_id for row in prior_put],
            "experimental_30_to_60_put": [row.replay_signal_id for row in experimental_put],
            "experimental_full": [row.replay_signal_id for row in experimental_full],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run controlled PUT 30-60 full replay")
    parser.add_argument("--baseline-report", type=Path, default=Path("data/extended_strategy_a_replay_report.json"))
    parser.add_argument("--prior-report", type=Path, default=Path("data/put_depth_full_replay_experiment.json"))
    parser.add_argument("--output", type=Path, default=Path("data/put_depth_30_60_full_replay_experiment.json"))
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps({
        "output": str(args.output),
        "experimental_put": result["experimental_put"]["statistics"],
        "full_strategy": result["full_strategy_effect"]["experimental_30_to_60"],
        "lifecycle_baseline": result["signal_lifecycle_comparison"]["summary_against_baseline"],
        "lifecycle_40_to_60": result["signal_lifecycle_comparison"]["summary_against_40_to_60"],
        "configuration_fingerprint": result["configuration_fingerprint"],
        "dataset_hash": result["data_fingerprint"]["dataset_hash"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
