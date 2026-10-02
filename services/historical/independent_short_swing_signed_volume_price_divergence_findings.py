"""Run the frozen signed-volume / price-divergence catch-up screen."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_short_swing_signed_volume_price_divergence_protocol import (
    DECISION_RULE,
    EXECUTION,
    FEATURE,
    GUARDRAILS,
    HYPOTHESIS,
    PRIMARY_RULE,
    PROTOCOL_VERSION,
    SOURCE_BLOCKS,
    SOURCE_COHORTS,
    SOURCE_EVENT_SHA256,
    SOURCE_EVENTS,
    SOURCE_PROTOCOL_VERSION,
    SOURCE_SESSIONS,
    STRUCTURAL_GATE,
)

RESEARCH_TYPE = "NIFTY_SHORT_SWING_SIGNED_VOLUME_PRICE_DIVERGENCE_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frozen_dataset(path: Path) -> dict[str, Any]:
    actual = _sha256(path)
    if actual != SOURCE_EVENT_SHA256:
        raise ValueError(
            f"V2 event dataset SHA256 changed: {actual} != {SOURCE_EVENT_SHA256}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    required_meta = {
        "protocol_version": SOURCE_PROTOCOL_VERSION,
        "sessions": SOURCE_SESSIONS,
        "five_minute_events": SOURCE_EVENTS,
        "blind_data_used": False,
        "implementation_allowed": False,
    }
    for key, expected in required_meta.items():
        if payload.get(key) != expected:
            raise ValueError(
                f"unexpected V2 event metadata {key}: "
                f"{payload.get(key)!r} != {expected!r}"
            )
    events = payload.get("events")
    if not isinstance(events, list) or len(events) != SOURCE_EVENTS:
        raise ValueError("V2 development events are missing or incomplete")
    return payload


def _frame(payload: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(payload["events"]).copy()
    required = {
        "cohort",
        "cohort_block",
        "timestamp",
        "date",
        "futures_return_bps",
        "futures_volume",
        "net_return_3_bps",
        "entry_timestamp",
        "entry_gap_bps",
    }
    for horizon in EXECUTION["fixed_exit_minutes"]:
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
        raise ValueError(f"V2 development event schema missing columns: {missing}")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["entry_timestamp"] = pd.to_datetime(frame["entry_timestamp"])
    for name in (
        "futures_return_bps",
        "futures_volume",
        "net_return_3_bps",
        "entry_gap_bps",
    ):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")

    frame = frame.sort_values(
        ["cohort", "date", "timestamp"]
    ).reset_index(drop=True)
    group_keys = [frame["cohort"], frame["date"]]
    signed_volume = (
        np.sign(frame["futures_return_bps"]) * frame["futures_volume"]
    )
    signed_sum = (
        signed_volume.groupby(group_keys)
        .rolling(window=3, min_periods=3)
        .sum()
        .reset_index(level=[0, 1], drop=True)
    )
    volume_sum = (
        frame["futures_volume"].groupby(group_keys)
        .rolling(window=3, min_periods=3)
        .sum()
        .reset_index(level=[0, 1], drop=True)
    )
    frame["signed_volume_imbalance_3"] = signed_sum / volume_sum.replace(0.0, np.nan)
    frame["abs_signed_volume_imbalance_3"] = frame[
        "signed_volume_imbalance_3"
    ].abs()
    frame["direction"] = np.sign(frame["signed_volume_imbalance_3"])
    frame["divergence"] = (
        frame["signed_volume_imbalance_3"]
        * frame["net_return_3_bps"]
    ).lt(0.0)
    return frame


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
    horizon_minutes: int,
) -> pd.DataFrame:
    prefix = f"h{horizon_minutes}m"
    threshold = float(PRIMARY_RULE["abs_signed_volume_imbalance_3_min"])
    mask = (
        frame["abs_signed_volume_imbalance_3"].ge(threshold)
        & frame["divergence"]
        & frame["direction"].ne(0.0)
        & frame["net_return_3_bps"].ne(0.0)
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
    return _non_overlapping(selected, horizon_minutes=horizon_minutes)


def _all_blocks(frame: pd.DataFrame) -> list[tuple[str, int]]:
    blocks = sorted({
        (str(row.cohort), int(row.cohort_block))
        for row in frame[["cohort", "cohort_block"]]
        .dropna()
        .itertuples(index=False)
    })
    if len(blocks) != SOURCE_BLOCKS:
        raise ValueError(
            f"expected {SOURCE_BLOCKS} chronological blocks, got {len(blocks)}"
        )
    return blocks


def _bootstrap(
    trades: pd.DataFrame,
    *,
    seed: int,
) -> list[float | None]:
    if trades.empty:
        return [None, None]
    grouped = trades.groupby("date")["aligned_terminal_bps"]
    sums = grouped.sum().to_numpy(dtype=float)
    counts = grouped.size().to_numpy(dtype=float)
    if len(sums) < 2:
        return [None, None]
    rng = np.random.default_rng(seed)
    draws = int(STRUCTURAL_GATE["bootstrap_samples"])
    index = rng.integers(0, len(sums), size=(draws, len(sums)))
    means = sums[index].sum(axis=1) / counts[index].sum(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return [float(lo), float(hi)]


def _summary(
    trades: pd.DataFrame,
    blocks: list[tuple[str, int]],
    *,
    seed: int,
) -> dict[str, Any]:
    trades_by_cohort = {
        cohort: int((trades["cohort"] == cohort).sum())
        for cohort in SOURCE_COHORTS
    }
    means = {}
    for cohort in SOURCE_COHORTS:
        values = trades.loc[
            trades["cohort"] == cohort, "aligned_terminal_bps"
        ]
        means[cohort] = float(values.mean()) if len(values) else None

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

    return {
        "trades": int(len(trades)),
        "trades_by_cohort": trades_by_cohort,
        "sessions_traded": int(trades["date"].nunique()) if len(trades) else 0,
        "pooled_mean_bps": (
            float(trades["aligned_terminal_bps"].mean()) if len(trades) else None
        ),
        "pooled_median_bps": (
            float(trades["aligned_terminal_bps"].median()) if len(trades) else None
        ),
        "cohort_mean_bps": means,
        "win_rate": (
            float((trades["aligned_terminal_bps"] > 0.0).mean())
            if len(trades) else None
        ),
        "mean_signed_volume_imbalance_3_abs": (
            float(trades["abs_signed_volume_imbalance_3"].mean())
            if len(trades) else None
        ),
        "mean_abs_net_return_3_bps": (
            float(trades["net_return_3_bps"].abs().mean())
            if len(trades) else None
        ),
        "mean_aligned_entry_gap_bps": (
            float(trades["aligned_entry_gap_bps"].mean())
            if len(trades) else None
        ),
        "mean_mfe_bps": (
            float(trades["aligned_mfe_bps"].mean()) if len(trades) else None
        ),
        "mean_mae_bps": (
            float(trades["aligned_mae_bps"].mean()) if len(trades) else None
        ),
        "chronological_block_means_bps": block_means,
        "positive_chronological_blocks": positive_blocks,
        "session_cluster_bootstrap_mean_95pct_bps": _bootstrap(
            trades, seed=seed
        ),
    }


def _passes(summary: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if summary["trades"] < int(STRUCTURAL_GATE["minimum_pooled_trades"]):
        failures.append("pooled_trade_count")
    for cohort in SOURCE_COHORTS:
        if summary["trades_by_cohort"][cohort] < int(
            STRUCTURAL_GATE["minimum_trades_each_cohort"]
        ):
            failures.append(f"{cohort}_trade_count")
        value = summary["cohort_mean_bps"][cohort]
        if value is None or value <= 0.0:
            failures.append(f"{cohort}_mean_not_positive")
    if summary["positive_chronological_blocks"] < int(
        STRUCTURAL_GATE["minimum_positive_chronological_blocks"]
    ):
        failures.append("chronological_block_stability")
    lower = summary["session_cluster_bootstrap_mean_95pct_bps"][0]
    if lower is None or lower <= 0.0:
        failures.append("bootstrap_lower_bound_not_positive")
    return not failures, failures


def run_screen(payload: dict[str, Any]) -> dict[str, Any]:
    frame = _frame(payload)
    blocks = _all_blocks(frame)
    formulations = []
    base_seed = int(STRUCTURAL_GATE["bootstrap_seed"])
    for counter, horizon in enumerate(
        EXECUTION["fixed_exit_minutes"], start=1
    ):
        trades = _select(frame, horizon_minutes=int(horizon))
        summary = _summary(
            trades, blocks, seed=base_seed + counter
        )
        passed, failures = _passes(summary)
        formulations.append({
            "formulation_id": f"svi3_divergence_catchup_h{int(horizon)}",
            "abs_signed_volume_imbalance_3_min": float(
                PRIMARY_RULE["abs_signed_volume_imbalance_3_min"]
            ),
            "divergence_required": True,
            "direction": "signed_volume_imbalance_3",
            "options_fast_lead_filter": "off",
            "OI_filter": "off",
            "exit_minutes": int(horizon),
            "summary": summary,
            "structural_gate_pass": passed,
            "structural_gate_failures": failures,
        })

    passes = [
        item["formulation_id"]
        for item in formulations
        if item["structural_gate_pass"]
    ]
    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "source": {
            "event_dataset_sha256": SOURCE_EVENT_SHA256,
            "event_protocol_version": SOURCE_PROTOCOL_VERSION,
            "sessions": SOURCE_SESSIONS,
            "five_minute_events": SOURCE_EVENTS,
            "cohorts": list(SOURCE_COHORTS),
            "chronological_blocks": SOURCE_BLOCKS,
        },
        "hypothesis": HYPOTHESIS,
        "feature": FEATURE,
        "primary_rule": PRIMARY_RULE,
        "execution": EXECUTION,
        "structural_gate": STRUCTURAL_GATE,
        "formulations": formulations,
        "structural_gate_passes": passes,
        "decision": (
            "STRUCTURAL_PASS_REQUIRES_SEPARATE_OPTIONS_PROTOCOL"
            if passes else "REJECTED_NO_CANDIDATE_FREEZE"
        ),
        "decision_rule": DECISION_RULE,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run frozen signed-volume / price-divergence screen"
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=Path(
            "data/independent_short_swing_development_events_v2.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/independent_short_swing_signed_volume_price_divergence_findings.json"
        ),
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
    }, indent=2))


if __name__ == "__main__":
    main()
