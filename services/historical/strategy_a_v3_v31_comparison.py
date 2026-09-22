"""Exact production-path A/B comparison for Strategy A V3 momentum decay.

Compares the frozen production V3 config against one controlled candidate that
changes only momentum_adx_min_delta_2bars from -2.0 to -2.5. Both variants run
through the production TrendPullback state machine and corrected lifecycle replay
over the same cached historical sessions.

This is research-only. It does not modify persisted production configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from services.historical.strategy_a_data_audit import _default_db_path
from services.historical.strategy_a_research import _open_read_only, _session_dates
from services.historical.strategy_a_state_machine_audit import audit_state_machine
from services.historical.strategy_a_v3_lifecycle_batch import _aggregate, run_batch
from services.strategy.models import HistoricalReplaySource, StrategyTunablesConfig


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _session_count(db_path: Path, *, source: str) -> int:
    conn = _open_read_only(db_path)
    try:
        return len(_session_dates(conn, sessions=0, source=source))
    finally:
        conn.close()


def _signal_dates(report: dict[str, Any]) -> set[str]:
    return set((report.get("aggregate") or {}).get("signal_dates") or [])


def _records_for_dates(
    lifecycle: dict[str, Any],
    dates: set[str],
) -> list[dict[str, Any]]:
    return [
        row
        for row in lifecycle.get("records") or []
        if str(row.get("trading_date")) in dates
    ]


def _variant_summary(
    state: dict[str, Any],
    lifecycle: dict[str, Any],
) -> dict[str, Any]:
    sessions = state.get("sessions") or []
    usable = [row for row in sessions if not row.get("skip_reason")]
    signal_dates = _signal_dates(state)
    aggregate = lifecycle.get("strategy_a") or {}
    return {
        "usable_sessions": len(usable),
        "setup_count": int((state.get("aggregate") or {}).get("setup_count") or 0),
        "signal_count": int((state.get("aggregate") or {}).get("signal_count") or 0),
        "signal_days": len(signal_dates),
        "usable_sessions_per_signal_day": (
            round(len(usable) / len(signal_dates), 2) if signal_dates else None
        ),
        "lifecycle": aggregate,
        "signal_parity": lifecycle.get("signal_parity") or {},
    }


async def run_comparison(
    db_path: Path,
    *,
    source: str,
    output_dir: Path,
) -> dict[str, Any]:
    sessions = _session_count(db_path, source=source)
    if sessions < 1:
        raise RuntimeError(f"no cached {source} sessions found")

    baseline_cfg = StrategyTunablesConfig()
    candidate_cfg = baseline_cfg.model_copy(
        update={"momentum_adx_min_delta_2bars": -2.5}
    )

    baseline_state = audit_state_machine(
        db_path,
        sessions=sessions,
        source=source,
        config=baseline_cfg,
    )
    candidate_state = audit_state_machine(
        db_path,
        sessions=sessions,
        source=source,
        config=candidate_cfg,
    )

    baseline_state_path = output_dir / "v3_state_machine.json"
    candidate_state_path = output_dir / "v31_state_machine.json"
    _write_json(baseline_state_path, baseline_state)
    _write_json(candidate_state_path, candidate_state)

    replay_source = HistoricalReplaySource(source)
    baseline_lifecycle = await run_batch(
        baseline_state_path,
        db_path=db_path,
        source=replay_source,
        tunables=baseline_cfg,
    )
    candidate_lifecycle = await run_batch(
        candidate_state_path,
        db_path=db_path,
        source=replay_source,
        tunables=candidate_cfg,
    )

    baseline_lifecycle_path = output_dir / "v3_lifecycle.json"
    candidate_lifecycle_path = output_dir / "v31_lifecycle.json"
    _write_json(baseline_lifecycle_path, baseline_lifecycle)
    _write_json(candidate_lifecycle_path, candidate_lifecycle)

    baseline_dates = _signal_dates(baseline_state)
    candidate_dates = _signal_dates(candidate_state)
    added_dates = candidate_dates - baseline_dates
    removed_dates = baseline_dates - candidate_dates
    common_dates = baseline_dates & candidate_dates

    added_records = _records_for_dates(candidate_lifecycle, added_dates)
    common_candidate_records = _records_for_dates(candidate_lifecycle, common_dates)
    common_baseline_records = _records_for_dates(baseline_lifecycle, common_dates)

    report = {
        "report_type": "STRATEGY_A_V3_V31_EXACT_PRODUCTION_AB_COMPARISON",
        "source": source.upper(),
        "cached_sessions": sessions,
        "production_config_changed": False,
        "variants": {
            "v3": {
                "momentum_adx_min_delta_2bars": baseline_cfg.momentum_adx_min_delta_2bars,
                "summary": _variant_summary(baseline_state, baseline_lifecycle),
            },
            "v31_candidate": {
                "momentum_adx_min_delta_2bars": candidate_cfg.momentum_adx_min_delta_2bars,
                "summary": _variant_summary(candidate_state, candidate_lifecycle),
            },
        },
        "signal_date_comparison": {
            "common_dates": sorted(common_dates),
            "added_by_v31": sorted(added_dates),
            "removed_by_v31": sorted(removed_dates),
            "common_count": len(common_dates),
            "added_count": len(added_dates),
            "removed_count": len(removed_dates),
        },
        "incremental_v31_trades": _aggregate(added_records),
        "common_signal_dates": {
            "v3": _aggregate(common_baseline_records),
            "v31": _aggregate(common_candidate_records),
        },
        "decision_support": {
            "candidate_is_frequency_expansion_only": True,
            "only_changed_parameter": "momentum_adx_min_delta_2bars",
            "baseline_value": -2.0,
            "candidate_value": -2.5,
            "promotion_is_automatic": False,
            "notes": [
                "Prefer added frequency only if incremental trades are not materially negative.",
                "Compare max drawdown and average R, not just total R or win rate.",
                "Any production change requires a separate explicit config/spec update after review.",
                "Lifecycle R is futures/underlying R; historical executable option P&L remains unavailable.",
            ],
        },
        "outputs": {
            "v3_state_machine": str(baseline_state_path),
            "v31_state_machine": str(candidate_state_path),
            "v3_lifecycle": str(baseline_lifecycle_path),
            "v31_lifecycle": str(candidate_lifecycle_path),
        },
    }
    summary_path = output_dir / "strategy_a_v3_v31_comparison.json"
    report["outputs"]["summary"] = str(summary_path)
    _write_json(summary_path, report)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exact production-path Strategy A V3 vs V3.1 (-2.5 ADX decay) comparison"
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
        default=Path("data") / "strategy_a_v3_v31_comparison",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = asyncio.run(
        run_comparison(
            args.db_path,
            source=args.source,
            output_dir=args.output_dir,
        )
    )
    compact = {
        "report_type": report["report_type"],
        "cached_sessions": report["cached_sessions"],
        "variants": report["variants"],
        "signal_date_comparison": report["signal_date_comparison"],
        "incremental_v31_trades": report["incremental_v31_trades"],
        "outputs": report["outputs"],
    }
    print(json.dumps(compact, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
