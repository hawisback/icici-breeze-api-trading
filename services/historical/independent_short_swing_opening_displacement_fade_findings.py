"""Run the frozen NIFTY opening-displacement fade development screen."""
from __future__ import annotations

import argparse
import json
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
    load_frozen_dataset,
)
from services.historical.independent_short_swing_opening_displacement_fade_protocol import (
    EXECUTION,
    GUARDRAILS,
    HYPOTHESIS,
    PROTOCOL_VERSION,
    SEARCH_GRID,
    SOURCE,
    STRUCTURAL_GATE,
    THRESHOLD_POLICY,
)

RESEARCH_TYPE = "NIFTY_SHORT_SWING_OPENING_DISPLACEMENT_FADE_FINDINGS_V1"


def _add_opening_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"]).dt.tz_localize(None)
    result["spot_close"] = pd.to_numeric(result["spot_close"], errors="coerce")
    result = result.sort_values(["cohort", "date", "timestamp"]).reset_index(drop=True)

    sessions = (
        result.groupby(["cohort", "date"], sort=False)
        .agg(
            first_timestamp=("timestamp", "first"),
            first_spot_close=("spot_close", "first"),
            final_spot_close=("spot_close", "last"),
        )
        .reset_index()
    )
    sessions["prior_session_final_spot_close"] = (
        sessions.groupby("cohort", sort=False)["final_spot_close"].shift(1)
    )
    sessions["opening_displacement_bps"] = (
        sessions["first_spot_close"] / sessions["prior_session_final_spot_close"] - 1.0
    ) * 10000.0
    sessions["abs_opening_displacement_bps"] = (
        sessions["opening_displacement_bps"].abs()
    )
    sessions["direction"] = -np.sign(sessions["opening_displacement_bps"])

    features = sessions[
        [
            "cohort", "date", "first_timestamp",
            "prior_session_final_spot_close", "opening_displacement_bps",
            "abs_opening_displacement_bps", "direction",
        ]
    ].rename(columns={"first_timestamp": "timestamp"})
    result = result.merge(
        features,
        on=["cohort", "date", "timestamp"],
        how="left",
        validate="many_to_one",
    )
    result["is_first_completed_bar"] = result["opening_displacement_bps"].notna()
    return result


def _frame(payload: dict[str, Any]) -> pd.DataFrame:
    if payload.get("blind_data_used") is not False:
        raise ValueError("development event input must have blind_data_used=false")
    frame = pd.DataFrame(payload["events"]).copy()
    required = {
        "cohort", "cohort_block", "timestamp", "date", "spot_close",
        "entry_timestamp", "entry_gap_bps",
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
    frame["entry_timestamp"] = pd.to_datetime(frame["entry_timestamp"]).dt.tz_localize(None)
    frame["entry_gap_bps"] = pd.to_numeric(frame["entry_gap_bps"], errors="coerce")
    return _add_opening_features(frame)


def _threshold(frame: pd.DataFrame) -> float:
    first = frame.loc[frame["is_first_completed_bar"], "abs_opening_displacement_bps"]
    values = first.dropna().to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if len(values) != int(SOURCE["sessions"]) - 2:
        raise ValueError(
            f"expected {int(SOURCE['sessions']) - 2} eligible session openings, got {len(values)}"
        )
    percentile = float(SEARCH_GRID["abs_opening_displacement_percentile_min"][0])
    return float(np.percentile(values, percentile))


def _select(
    frame: pd.DataFrame,
    *,
    threshold_bps: float,
    horizon_minutes: int,
) -> pd.DataFrame:
    prefix = f"h{horizon_minutes}m"
    mask = (
        frame["is_first_completed_bar"]
        & frame["abs_opening_displacement_bps"].ge(threshold_bps)
        & frame["direction"].ne(0.0)
        & frame[f"{prefix}_terminal_bps"].notna()
    )
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
    if selected.duplicated(["cohort", "date"]).any():
        raise ValueError("opening-displacement screen produced more than one trade per session")
    return selected


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
        "mean_abs_opening_displacement_bps": (
            float(trades["abs_opening_displacement_bps"].mean()) if len(trades) else None
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
    threshold = _threshold(frame)
    blocks = _all_blocks(frame)
    formulations = []
    for counter, horizon in enumerate(SEARCH_GRID["fixed_exit_minutes"], start=1):
        trades = _select(
            frame,
            threshold_bps=threshold,
            horizon_minutes=int(horizon),
        )
        summary = _summary(trades, blocks, BOOTSTRAP_SEED + 200 + counter)
        passed, failures = _passes(summary)
        formulations.append({
            "formulation_id": f"odp25_opt_off_h{int(horizon)}",
            "abs_opening_displacement_percentile_min": 25,
            "derived_abs_opening_displacement_threshold_bps": threshold,
            "options_fast_lead_filter": "off",
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
        "derived_abs_opening_displacement_threshold_bps": threshold,
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
    parser = argparse.ArgumentParser(
        description="Run opening-displacement fade development screen"
    )
    parser.add_argument(
        "--events", type=Path,
        default=Path("data/independent_short_swing_development_events.json"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/independent_short_swing_opening_displacement_fade_findings.json"),
    )
    args = parser.parse_args()
    payload = load_frozen_dataset(args.events)
    report = run_screen(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "decision": report["decision"],
        "structural_gate_passes": report["structural_gate_passes"],
        "derived_abs_opening_displacement_threshold_bps": report[
            "derived_abs_opening_displacement_threshold_bps"
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
