"""Run the predeclared directional-efficiency continuation development screen.

This module consumes only the frozen inspected 152-session short-swing event
corpus. It does not read blind data, optimize stops/targets, or inspect exact
option P&L. A structural pass only permits a separate option protocol to be
frozen before any option implementation outcomes are examined.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_short_swing_development_findings import (
    SHORT_SWING_DEVELOPMENT_FINDINGS_V1,
)
from services.historical.independent_short_swing_directional_efficiency_protocol import (
    CONTROL_COMPARISON,
    EXECUTION,
    GUARDRAILS,
    HYPOTHESIS,
    PROTOCOL_VERSION,
    SEARCH_GRID,
    SOURCE,
    STRUCTURAL_GATE,
    THRESHOLD_POLICY,
)

RESEARCH_TYPE = "NIFTY_SHORT_SWING_DIRECTIONAL_EFFICIENCY_FINDINGS_V1"
BOOTSTRAP_SEED = 20260929
BOOTSTRAP_DRAWS = 5000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frozen_dataset(path: Path) -> dict[str, Any]:
    actual_sha = _sha256(path)
    expected_sha = str(SOURCE["event_dataset_sha256"])
    if actual_sha != expected_sha:
        raise ValueError(
            f"event dataset SHA256 changed: {actual_sha} != {expected_sha}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("sessions", -1)) != int(SOURCE["sessions"]):
        raise ValueError("unexpected development session count")
    if int(payload.get("five_minute_events", -1)) != int(
        SOURCE["five_minute_events"]
    ):
        raise ValueError("unexpected development event count")
    if payload.get("blind_data_used") is not False:
        raise ValueError("input must be the inspected development corpus")
    events = payload.get("events")
    if not isinstance(events, list) or len(events) != int(
        SOURCE["five_minute_events"]
    ):
        raise ValueError("development events are missing or incomplete")
    return payload


def _frame(payload: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(payload["events"]).copy()
    required = {
        "cohort",
        "cohort_block",
        "timestamp",
        "date",
        "net_return_3_bps",
        "path_length_3_bps",
        "options_specific_fast_lead",
        "entry_timestamp",
        "entry_gap_bps",
    }
    for horizon in SEARCH_GRID["fixed_exit_minutes"]:
        prefix = f"h{horizon}m"
        required.update(
            {
                f"{prefix}_terminal_bps",
                f"{prefix}_long_mfe_bps",
                f"{prefix}_long_mae_bps",
                f"{prefix}_short_mfe_bps",
                f"{prefix}_short_mae_bps",
            }
        )
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"development event schema missing columns: {missing}")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["entry_timestamp"] = pd.to_datetime(frame["entry_timestamp"])
    frame["net_return_3_bps"] = pd.to_numeric(
        frame["net_return_3_bps"], errors="coerce"
    )
    frame["path_length_3_bps"] = pd.to_numeric(
        frame["path_length_3_bps"], errors="coerce"
    )
    frame["abs_net_return_3_bps"] = frame["net_return_3_bps"].abs()
    frame["direction"] = np.sign(frame["net_return_3_bps"])

    denominator = frame["path_length_3_bps"].where(
        frame["path_length_3_bps"] > 0.0
    )
    frame["directional_efficiency"] = (
        frame["abs_net_return_3_bps"] / denominator
    ).clip(lower=0.0, upper=1.0)
    return frame.sort_values(["date", "entry_timestamp"]).reset_index(drop=True)


def _thresholds(frame: pd.DataFrame) -> dict[int, float]:
    values = frame["abs_net_return_3_bps"].dropna().to_numpy(dtype=float)
    if not len(values):
        raise ValueError("no finite three-bar net returns")
    return {
        int(percentile): float(np.percentile(values, float(percentile)))
        for percentile in SEARCH_GRID["abs_net_return_3_percentile_min"]
    }


def _non_overlapping(
    frame: pd.DataFrame,
    *,
    horizon_minutes: int,
) -> pd.DataFrame:
    accepted: list[int] = []
    for _, group in frame.sort_values(
        ["date", "entry_timestamp"]
    ).groupby("date", sort=True):
        blocked_until: pd.Timestamp | None = None
        for index, row in group.iterrows():
            entry = row["entry_timestamp"]
            if pd.isna(entry):
                continue
            if blocked_until is not None and entry < blocked_until:
                continue
            accepted.append(int(index))
            blocked_until = entry + pd.Timedelta(minutes=horizon_minutes)
    return frame.loc[accepted].sort_values(
        ["date", "entry_timestamp"]
    ).reset_index(drop=True)


def _select(
    frame: pd.DataFrame,
    *,
    abs_net_threshold_bps: float,
    efficiency_min: float | None,
    efficiency_max: float | None,
    options_filter: str,
    horizon_minutes: int,
) -> pd.DataFrame:
    prefix = f"h{horizon_minutes}m"
    mask = (
        frame["abs_net_return_3_bps"].ge(abs_net_threshold_bps)
        & frame["direction"].ne(0.0)
        & frame["directional_efficiency"].notna()
        & frame[f"{prefix}_terminal_bps"].notna()
    )
    if efficiency_min is not None:
        mask &= frame["directional_efficiency"].ge(efficiency_min)
    if efficiency_max is not None:
        mask &= frame["directional_efficiency"].le(efficiency_max)

    if options_filter == "directional_agreement":
        mask &= (
            frame["options_specific_fast_lead"].notna()
            & (
                frame["options_specific_fast_lead"]
                * frame["direction"]
            ).gt(0.0)
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


def _session_cluster_bootstrap(
    trades: pd.DataFrame,
    *,
    seed: int,
) -> list[float | None]:
    if trades.empty:
        return [None, None]

    grouped = trades.groupby("date")["aligned_terminal_bps"]
    session_sums = grouped.sum().to_numpy(dtype=float)
    session_counts = grouped.size().to_numpy(dtype=float)
    n_sessions = len(session_sums)
    if n_sessions < 2:
        return [None, None]

    rng = np.random.default_rng(seed)
    indices = rng.integers(
        0, n_sessions, size=(BOOTSTRAP_DRAWS, n_sessions)
    )
    sampled_sums = session_sums[indices].sum(axis=1)
    sampled_counts = session_counts[indices].sum(axis=1)
    means = sampled_sums / sampled_counts
    lower, upper = np.quantile(means, [0.025, 0.975])
    return [float(lower), float(upper)]


def _block_labels(frame: pd.DataFrame) -> list[tuple[str, int]]:
    labels = {
        (str(row.cohort), int(row.cohort_block))
        for row in frame[["cohort", "cohort_block"]]
        .dropna()
        .itertuples(index=False)
    }
    return sorted(labels, key=lambda item: (item[0], item[1]))


def _summarize(
    trades: pd.DataFrame,
    *,
    all_blocks: list[tuple[str, int]],
    seed: int,
) -> dict[str, Any]:
    by_cohort = {
        cohort: int((trades["cohort"] == cohort).sum())
        for cohort in ("cohort1", "cohort2")
    }
    cohort_means: dict[str, float | None] = {}
    for cohort in ("cohort1", "cohort2"):
        values = trades.loc[
            trades["cohort"] == cohort, "aligned_terminal_bps"
        ]
        cohort_means[cohort] = (
            float(values.mean()) if len(values) else None
        )

    block_means: dict[str, float | None] = {}
    positive_blocks = 0
    for cohort, block in all_blocks:
        values = trades.loc[
            (trades["cohort"] == cohort)
            & (trades["cohort_block"].astype("Int64") == block),
            "aligned_terminal_bps",
        ]
        value = float(values.mean()) if len(values) else None
        block_means[f"{cohort}_block_{block}"] = value
        if value is not None and value > 0.0:
            positive_blocks += 1

    bootstrap = _session_cluster_bootstrap(trades, seed=seed)
    pooled_mean = (
        float(trades["aligned_terminal_bps"].mean())
        if len(trades)
        else None
    )
    reference_cost = float(
        SHORT_SWING_DEVELOPMENT_FINDINGS_V1["family_findings"]
        ["failed_breakout_reversal"]["futures_cost_screen"]
        ["representative_current_roundtrip_cost_bps_before_slippage"]
    )

    return {
        "trades": int(len(trades)),
        "trades_by_cohort": by_cohort,
        "sessions_traded": int(trades["date"].nunique()),
        "trades_per_session": float(len(trades) / int(SOURCE["sessions"])),
        "pooled_mean_bps": pooled_mean,
        "pooled_median_bps": (
            float(trades["aligned_terminal_bps"].median())
            if len(trades)
            else None
        ),
        "cohort1_mean_bps": cohort_means["cohort1"],
        "cohort2_mean_bps": cohort_means["cohort2"],
        "win_rate": (
            float((trades["aligned_terminal_bps"] > 0.0).mean())
            if len(trades)
            else None
        ),
        "mean_mfe_bps": (
            float(trades["aligned_mfe_bps"].mean())
            if len(trades)
            else None
        ),
        "mean_mae_bps": (
            float(trades["aligned_mae_bps"].mean())
            if len(trades)
            else None
        ),
        "mean_aligned_entry_gap_bps": (
            float(trades["aligned_entry_gap_bps"].mean())
            if len(trades)
            else None
        ),
        "mean_abs_entry_gap_bps": (
            float(trades["entry_gap_bps"].abs().mean())
            if len(trades)
            else None
        ),
        "chronological_block_means_bps": block_means,
        "positive_chronological_blocks": int(positive_blocks),
        "session_cluster_bootstrap_mean_95pct_bps": bootstrap,
        "reference_current_futures_roundtrip_cost_bps_before_slippage": (
            reference_cost
        ),
        "gross_minus_reference_cost_bps": (
            float(pooled_mean - reference_cost)
            if pooled_mean is not None
            else None
        ),
    }


def _passes_gate(summary: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if summary["trades"] < int(STRUCTURAL_GATE["minimum_pooled_trades"]):
        reasons.append("pooled_trade_count")
    for cohort in ("cohort1", "cohort2"):
        if summary["trades_by_cohort"][cohort] < int(
            STRUCTURAL_GATE["minimum_trades_per_cohort"]
        ):
            reasons.append(f"{cohort}_trade_count")

    if STRUCTURAL_GATE["require_positive_mean_in_both_cohorts"]:
        for cohort in ("cohort1", "cohort2"):
            value = summary[f"{cohort}_mean_bps"]
            if value is None or value <= 0.0:
                reasons.append(f"{cohort}_mean_not_positive")

    if summary["positive_chronological_blocks"] < int(
        STRUCTURAL_GATE[
            "minimum_positive_chronological_blocks_out_of_14"
        ]
    ):
        reasons.append("chronological_block_stability")

    if STRUCTURAL_GATE[
        "require_pooled_session_cluster_bootstrap_95pct_lower_bound_gt_zero"
    ]:
        lower = summary[
            "session_cluster_bootstrap_mean_95pct_bps"
        ][0]
        if lower is None or lower <= 0.0:
            reasons.append("bootstrap_lower_bound_not_positive")

    return not reasons, reasons


def run_screen(payload: dict[str, Any]) -> dict[str, Any]:
    frame = _frame(payload)
    thresholds = _thresholds(frame)
    all_blocks = _block_labels(frame)
    if len(all_blocks) != 14:
        raise ValueError(f"expected 14 chronological blocks, got {len(all_blocks)}")

    formulations: list[dict[str, Any]] = []
    counter = 0
    for percentile, efficiency_min, options_filter, horizon in product(
        SEARCH_GRID["abs_net_return_3_percentile_min"],
        SEARCH_GRID["directional_efficiency_min"],
        SEARCH_GRID["options_fast_lead_filter"],
        SEARCH_GRID["fixed_exit_minutes"],
    ):
        counter += 1
        threshold = thresholds[int(percentile)]
        trades = _select(
            frame,
            abs_net_threshold_bps=threshold,
            efficiency_min=float(efficiency_min),
            efficiency_max=None,
            options_filter=str(options_filter),
            horizon_minutes=int(horizon),
        )
        summary = _summarize(
            trades, all_blocks=all_blocks, seed=BOOTSTRAP_SEED + counter
        )
        passed, failures = _passes_gate(summary)
        formulations.append(
            {
                "formulation_id": (
                    f"p{int(percentile)}_eff{int(float(efficiency_min) * 100)}_"
                    f"opt_{options_filter}_h{int(horizon)}"
                ),
                "abs_net_return_3_percentile_min": int(percentile),
                "derived_abs_net_return_3_threshold_bps": float(threshold),
                "directional_efficiency_min": float(efficiency_min),
                "options_fast_lead_filter": str(options_filter),
                "exit_minutes": int(horizon),
                "summary": summary,
                "structural_gate_pass": bool(passed),
                "structural_gate_failures": failures,
            }
        )

    controls: list[dict[str, Any]] = []
    control_counter = 1000
    for percentile, horizon in product(
        SEARCH_GRID["abs_net_return_3_percentile_min"],
        SEARCH_GRID["fixed_exit_minutes"],
    ):
        control_counter += 1
        threshold = thresholds[int(percentile)]
        trades = _select(
            frame,
            abs_net_threshold_bps=threshold,
            efficiency_min=None,
            efficiency_max=float(CONTROL_COMPARISON["low_efficiency_max"]),
            options_filter="off",
            horizon_minutes=int(horizon),
        )
        controls.append(
            {
                "abs_net_return_3_percentile_min": int(percentile),
                "derived_abs_net_return_3_threshold_bps": float(threshold),
                "directional_efficiency_max": float(
                    CONTROL_COMPARISON["low_efficiency_max"]
                ),
                "options_fast_lead_filter": "off",
                "exit_minutes": int(horizon),
                "summary": _summarize(
                    trades,
                    all_blocks=all_blocks,
                    seed=BOOTSTRAP_SEED + control_counter,
                ),
                "promotable": False,
            }
        )

    passes = [
        item["formulation_id"]
        for item in formulations
        if item["structural_gate_pass"]
    ]
    decision = (
        "STRUCTURAL_PASS_REQUIRES_SEPARATE_OPTIONS_PROTOCOL"
        if passes
        else "REJECTED_NO_CANDIDATE_FREEZE"
    )

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
        "derived_abs_net_return_3_thresholds_bps": {
            str(key): float(value) for key, value in thresholds.items()
        },
        "execution": EXECUTION,
        "structural_gate": STRUCTURAL_GATE,
        "formulations": formulations,
        "structural_gate_passes": passes,
        "low_efficiency_controls": controls,
        "decision": decision,
        "guardrails": GUARDRAILS,
        "next_research_decision": (
            "If and only if structural_gate_passes is non-empty, freeze a "
            "separate exact-option execution protocol before inspecting option "
            "implementation outcomes. Otherwise reject this hypothesis and move "
            "to a genuinely distinct predeclared behavioral hypothesis."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run directional-efficiency continuation development screen"
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=Path("data/independent_short_swing_development_events.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/independent_short_swing_directional_efficiency_findings.json"
        ),
    )
    args = parser.parse_args()

    payload = load_frozen_dataset(args.events)
    report = run_screen(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "protocol_version": report["protocol_version"],
                "decision": report["decision"],
                "structural_gate_passes": report["structural_gate_passes"],
                "derived_abs_net_return_3_thresholds_bps": report[
                    "derived_abs_net_return_3_thresholds_bps"
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
