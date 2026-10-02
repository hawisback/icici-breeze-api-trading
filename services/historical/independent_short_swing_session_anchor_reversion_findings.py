"""Run the predeclared session-anchor deviation reversion development screen."""
from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_short_swing_development_findings import (
    SHORT_SWING_DEVELOPMENT_FINDINGS_V1,
)
from services.historical.independent_short_swing_futures_spot_dislocation_findings import (
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    _non_overlapping,
    load_frozen_dataset,
)
from services.historical.independent_short_swing_session_anchor_reversion_protocol import (
    EXECUTION,
    GUARDRAILS,
    HYPOTHESIS,
    PROTOCOL_VERSION,
    SEARCH_GRID,
    SOURCE,
    STRUCTURAL_GATE,
    THRESHOLD_POLICY,
)

RESEARCH_TYPE = "NIFTY_SHORT_SWING_SESSION_ANCHOR_REVERSION_FINDINGS_V1"


def _add_anchor_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for name in ("futures_high", "futures_low", "futures_close", "futures_volume"):
        result[name] = pd.to_numeric(result[name], errors="coerce")
    if (result["futures_volume"].dropna() < 0).any():
        raise ValueError("futures volume cannot be negative")
    result["anchor_typical_price"] = (
        result["futures_high"] + result["futures_low"] + result["futures_close"]
    ) / 3.0
    result["anchor_pv"] = result["anchor_typical_price"] * result["futures_volume"]
    group = result.groupby("date", sort=False)
    cumulative_pv = group["anchor_pv"].cumsum()
    cumulative_volume = group["futures_volume"].cumsum()
    result["session_anchor"] = cumulative_pv / cumulative_volume.replace(0.0, np.nan)
    result["anchor_deviation_bps"] = (
        result["futures_close"] / result["session_anchor"] - 1.0
    ) * 10000.0
    result["abs_anchor_deviation_bps"] = result["anchor_deviation_bps"].abs()
    result["direction"] = -np.sign(result["anchor_deviation_bps"])
    return result


def _frame(payload: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(payload["events"]).copy()
    required = {
        "cohort", "cohort_block", "timestamp", "date",
        "futures_high", "futures_low", "futures_close", "futures_volume",
        "options_specific_fast_lead", "entry_timestamp", "entry_gap_bps",
    }
    for horizon in SEARCH_GRID["fixed_exit_minutes"]:
        prefix = f"h{int(horizon)}m"
        required.update({
            f"{prefix}_terminal_bps",
            f"{prefix}_long_mfe_bps",
            f"{prefix}_long_mae_bps",
            f"{prefix}_short_mfe_bps",
            f"{prefix}_short_mae_bps",
        })
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"development event schema missing columns: {missing}")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["entry_timestamp"] = pd.to_datetime(frame["entry_timestamp"])
    for name in ("options_specific_fast_lead", "entry_gap_bps"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.sort_values(["date", "timestamp"]).reset_index(drop=True)
    return _add_anchor_features(frame)


def _thresholds(frame: pd.DataFrame) -> dict[int, float]:
    values = frame["abs_anchor_deviation_bps"].dropna().to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        raise ValueError("no finite session-anchor deviations")
    return {
        int(p): float(np.percentile(values, float(p)))
        for p in SEARCH_GRID["abs_anchor_deviation_percentile_min"]
    }


def _select(
    frame: pd.DataFrame,
    *,
    threshold_bps: float,
    options_filter: str,
    horizon_minutes: int,
) -> pd.DataFrame:
    prefix = f"h{horizon_minutes}m"
    mask = (
        frame["abs_anchor_deviation_bps"].ge(threshold_bps)
        & frame["direction"].ne(0.0)
        & frame[f"{prefix}_terminal_bps"].notna()
    )
    if options_filter == "reversion_direction_agreement":
        mask &= (
            frame["options_specific_fast_lead"].notna()
            & (frame["options_specific_fast_lead"] * frame["direction"]).gt(0.0)
        )
    elif options_filter != "off":
        raise ValueError(f"unknown options filter {options_filter!r}")

    selected = frame.loc[mask].copy()
    selected["aligned_terminal_bps"] = (
        selected["direction"] * selected[f"{prefix}_terminal_bps"]
    )
    selected["aligned_entry_gap_bps"] = (
        selected["direction"] * selected["entry_gap_bps"]
    )
    selected["aligned_mfe_bps"] = np.where(
        selected["direction"] > 0,
        selected[f"{prefix}_long_mfe_bps"],
        selected[f"{prefix}_short_mfe_bps"],
    )
    selected["aligned_mae_bps"] = np.where(
        selected["direction"] > 0,
        selected[f"{prefix}_long_mae_bps"],
        selected[f"{prefix}_short_mae_bps"],
    )
    return _non_overlapping(selected, horizon_minutes=horizon_minutes)


def _bootstrap(trades: pd.DataFrame, seed: int) -> list[float | None]:
    if trades.empty:
        return [None, None]
    grouped = trades.groupby("date")["aligned_terminal_bps"]
    sums = grouped.sum().to_numpy(dtype=float)
    counts = grouped.size().to_numpy(dtype=float)
    if len(sums) < 2:
        return [None, None]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(sums), size=(BOOTSTRAP_DRAWS, len(sums)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return [float(lo), float(hi)]


def _all_blocks(frame: pd.DataFrame) -> list[tuple[str, int]]:
    labels = {
        (str(row.cohort), int(row.cohort_block))
        for row in frame[["cohort", "cohort_block"]].dropna().itertuples(index=False)
    }
    result = sorted(labels, key=lambda item: (item[0], item[1]))
    if len(result) != 14:
        raise ValueError(f"expected 14 chronological blocks, got {len(result)}")
    return result


def _summary(
    trades: pd.DataFrame,
    blocks: list[tuple[str, int]],
    seed: int,
) -> dict[str, Any]:
    trades_by_cohort = {
        cohort: int((trades["cohort"] == cohort).sum())
        for cohort in ("cohort1", "cohort2")
    }
    cohort_means = {}
    for cohort in ("cohort1", "cohort2"):
        values = trades.loc[trades["cohort"] == cohort, "aligned_terminal_bps"]
        cohort_means[cohort] = float(values.mean()) if len(values) else None

    block_means: dict[str, float | None] = {}
    positive_blocks = 0
    for cohort, block in blocks:
        values = trades.loc[
            (trades["cohort"] == cohort)
            & (trades["cohort_block"].astype("Int64") == block),
            "aligned_terminal_bps",
        ]
        value = float(values.mean()) if len(values) else None
        block_means[f"{cohort}_block_{block}"] = value
        if value is not None and value > 0.0:
            positive_blocks += 1

    pooled = float(trades["aligned_terminal_bps"].mean()) if len(trades) else None
    reference_cost = float(
        SHORT_SWING_DEVELOPMENT_FINDINGS_V1["family_findings"]
        ["failed_breakout_reversal"]["futures_cost_screen"]
        ["representative_current_roundtrip_cost_bps_before_slippage"]
    )
    return {
        "trades": int(len(trades)),
        "trades_by_cohort": trades_by_cohort,
        "sessions_traded": int(trades["date"].nunique()),
        "pooled_mean_bps": pooled,
        "pooled_median_bps": (
            float(trades["aligned_terminal_bps"].median()) if len(trades) else None
        ),
        "cohort1_mean_bps": cohort_means["cohort1"],
        "cohort2_mean_bps": cohort_means["cohort2"],
        "win_rate": (
            float((trades["aligned_terminal_bps"] > 0).mean()) if len(trades) else None
        ),
        "mean_mfe_bps": (
            float(trades["aligned_mfe_bps"].mean()) if len(trades) else None
        ),
        "mean_mae_bps": (
            float(trades["aligned_mae_bps"].mean()) if len(trades) else None
        ),
        "mean_abs_anchor_deviation_bps": (
            float(trades["abs_anchor_deviation_bps"].mean()) if len(trades) else None
        ),
        "mean_aligned_entry_gap_bps": (
            float(trades["aligned_entry_gap_bps"].mean()) if len(trades) else None
        ),
        "chronological_block_means_bps": block_means,
        "positive_chronological_blocks": positive_blocks,
        "session_cluster_bootstrap_mean_95pct_bps": _bootstrap(trades, seed),
        "reference_current_futures_roundtrip_cost_bps_before_slippage": reference_cost,
        "gross_minus_reference_cost_bps": (
            float(pooled - reference_cost) if pooled is not None else None
        ),
    }


def _passes(summary: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if summary["trades"] < int(STRUCTURAL_GATE["minimum_pooled_trades"]):
        failures.append("pooled_trade_count")
    for cohort in ("cohort1", "cohort2"):
        if summary["trades_by_cohort"][cohort] < int(
            STRUCTURAL_GATE["minimum_trades_per_cohort"]
        ):
            failures.append(f"{cohort}_trade_count")
        value = summary[f"{cohort}_mean_bps"]
        if value is None or value <= 0:
            failures.append(f"{cohort}_mean_not_positive")
    if summary["positive_chronological_blocks"] < int(
        STRUCTURAL_GATE["minimum_positive_chronological_blocks_out_of_14"]
    ):
        failures.append("chronological_block_stability")
    lower = summary["session_cluster_bootstrap_mean_95pct_bps"][0]
    if lower is None or lower <= 0:
        failures.append("bootstrap_lower_bound_not_positive")
    return not failures, failures


def run_screen(payload: dict[str, Any]) -> dict[str, Any]:
    frame = _frame(payload)
    thresholds = _thresholds(frame)
    blocks = _all_blocks(frame)
    formulations = []
    counter = 0
    for percentile, options_filter, horizon in product(
        SEARCH_GRID["abs_anchor_deviation_percentile_min"],
        SEARCH_GRID["options_fast_lead_filter"],
        SEARCH_GRID["fixed_exit_minutes"],
    ):
        counter += 1
        threshold = thresholds[int(percentile)]
        trades = _select(
            frame,
            threshold_bps=threshold,
            options_filter=str(options_filter),
            horizon_minutes=int(horizon),
        )
        summary = _summary(trades, blocks, BOOTSTRAP_SEED + 100 + counter)
        passed, failures = _passes(summary)
        formulations.append({
            "formulation_id": (
                f"ap{int(percentile)}_opt_{options_filter}_h{int(horizon)}"
            ),
            "abs_anchor_deviation_percentile_min": int(percentile),
            "derived_abs_anchor_deviation_threshold_bps": threshold,
            "options_fast_lead_filter": str(options_filter),
            "exit_minutes": int(horizon),
            "summary": summary,
            "structural_gate_pass": passed,
            "structural_gate_failures": failures,
        })
    passes = [
        item["formulation_id"] for item in formulations if item["structural_gate_pass"]
    ]
    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "source": SOURCE,
        "hypothesis": HYPOTHESIS,
        "search_grid": SEARCH_GRID,
        "threshold_policy": THRESHOLD_POLICY,
        "derived_abs_anchor_deviation_thresholds_bps": {
            str(k): v for k, v in thresholds.items()
        },
        "execution": EXECUTION,
        "structural_gate": STRUCTURAL_GATE,
        "formulations": formulations,
        "structural_gate_passes": passes,
        "decision": (
            "STRUCTURAL_PASS_REQUIRES_SEPARATE_OPTIONS_PROTOCOL"
            if passes else "REJECTED_NO_CANDIDATE_FREEZE"
        ),
        "guardrails": GUARDRAILS,
        "next_research_decision": (
            "If structural_gate_passes is non-empty, freeze a separate exact-option "
            "protocol before inspecting option outcomes. Otherwise reject without rescue."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run session-anchor reversion development screen")
    parser.add_argument(
        "--events", type=Path,
        default=Path("data/independent_short_swing_development_events.json"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/independent_short_swing_session_anchor_reversion_findings.json"),
    )
    args = parser.parse_args()
    payload = load_frozen_dataset(args.events)
    report = run_screen(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "decision": report["decision"],
        "structural_gate_passes": report["structural_gate_passes"],
        "derived_abs_anchor_deviation_thresholds_bps": report[
            "derived_abs_anchor_deviation_thresholds_bps"
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
