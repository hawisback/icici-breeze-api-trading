"""Run the frozen externally motivated open-to-close intraday momentum screen."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_short_swing_external_open_close_intraday_momentum_protocol import (
    DECISION_RULE,
    EXECUTION,
    EXTERNAL_SOURCES,
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

RESEARCH_TYPE = "NIFTY_SHORT_SWING_EXTERNAL_OPEN_CLOSE_INTRADAY_MOMENTUM_FINDINGS_V1"


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
    required = {
        "protocol_version": SOURCE_PROTOCOL_VERSION,
        "sessions": SOURCE_SESSIONS,
        "five_minute_events": SOURCE_EVENTS,
        "blind_data_used": False,
        "implementation_allowed": False,
    }
    for key, expected in required.items():
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
        "net_return_6_bps",
        "entry_timestamp",
        "entry_gap_bps",
        "h30_terminal_bps",
        "h30_long_mfe_bps",
        "h30_long_mae_bps",
        "h30_short_mfe_bps",
        "h30_short_mae_bps",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"V2 development event schema missing columns: {missing}")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["entry_timestamp"] = pd.to_datetime(frame["entry_timestamp"])
    for name in (
        "net_return_6_bps",
        "entry_gap_bps",
        "h30_terminal_bps",
        "h30_long_mfe_bps",
        "h30_long_mae_bps",
        "h30_short_mfe_bps",
        "h30_short_mae_bps",
    ):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")

    return frame.sort_values(
        ["cohort", "date", "timestamp"]
    ).reset_index(drop=True)


def _signal_trades(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.Series] = []

    for (cohort, day), group in frame.groupby(
        ["cohort", "date"], sort=True
    ):
        ordered = group.sort_values("timestamp")
        if len(ordered) != 75:
            raise ValueError(
                f"{cohort} {day} expected 75 five-minute rows, got {len(ordered)}"
            )

        by_time = {
            value.strftime("%H:%M"): row
            for value, (_, row) in zip(
                ordered["timestamp"], ordered.iterrows()
            )
        }
        if "09:40" not in by_time:
            raise ValueError(f"{cohort} {day} missing 09:40 signal row")
        if "14:55" not in by_time:
            raise ValueError(f"{cohort} {day} missing 14:55 trade row")

        signal = by_time["09:40"]
        trade = by_time["14:55"].copy()

        opening_half_hour_bps = signal["net_return_6_bps"]
        if pd.isna(opening_half_hour_bps):
            raise ValueError(
                f"{cohort} {day} opening-half-hour return unexpectedly missing"
            )
        direction = float(np.sign(float(opening_half_hour_bps)))
        if direction == 0.0:
            continue

        if trade["entry_timestamp"].strftime("%H:%M") != "15:00":
            raise ValueError(
                f"{cohort} {day} 14:55 row does not enter at 15:00"
            )
        if pd.isna(trade["h30_terminal_bps"]):
            raise ValueError(
                f"{cohort} {day} 14:55 row missing 30-minute closing outcome"
            )

        trade["opening_half_hour_return_bps"] = float(opening_half_hour_bps)
        trade["direction"] = direction
        rows.append(trade)

    if not rows:
        return pd.DataFrame()

    trades = pd.DataFrame(rows).reset_index(drop=True)
    if trades["date"].duplicated().any():
        raise ValueError("external intraday momentum generated multiple trades per session")
    return trades.sort_values(
        ["date", "entry_timestamp"]
    ).reset_index(drop=True)


def _align(trades: pd.DataFrame) -> pd.DataFrame:
    result = trades.copy()
    result["aligned_terminal_bps"] = (
        result["direction"] * result["h30_terminal_bps"]
    )
    result["aligned_entry_gap_bps"] = (
        result["direction"] * result["entry_gap_bps"]
    )
    result["aligned_mfe_bps"] = np.where(
        result["direction"] > 0,
        result["h30_long_mfe_bps"],
        result["h30_short_mfe_bps"],
    )
    result["aligned_mae_bps"] = np.where(
        result["direction"] > 0,
        result["h30_long_mae_bps"],
        result["h30_short_mae_bps"],
    )
    return result


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


def _bootstrap(trades: pd.DataFrame, *, seed: int) -> list[float | None]:
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
) -> dict[str, Any]:
    trades_by_cohort = {
        cohort: int((trades["cohort"] == cohort).sum())
        for cohort in SOURCE_COHORTS
    }
    cohort_means: dict[str, float | None] = {}
    for cohort in SOURCE_COHORTS:
        values = trades.loc[
            trades["cohort"] == cohort, "aligned_terminal_bps"
        ]
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
        "cohort_mean_bps": cohort_means,
        "win_rate": (
            float((trades["aligned_terminal_bps"] > 0.0).mean())
            if len(trades) else None
        ),
        "mean_abs_opening_half_hour_return_bps": (
            float(trades["opening_half_hour_return_bps"].abs().mean())
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
            trades, seed=int(STRUCTURAL_GATE["bootstrap_seed"])
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
    trades = _align(_signal_trades(frame))
    blocks = _all_blocks(frame)
    summary = _summary(trades, blocks)
    passed, failures = _passes(summary)

    formulation = {
        "formulation_id": "external_open30_to_close30_momentum",
        "signal_row_time": PRIMARY_RULE["signal_row_time"],
        "trade_row_time": PRIMARY_RULE["trade_row_time"],
        "entry_time": PRIMARY_RULE["entry_time"],
        "exit_time": PRIMARY_RULE["exit_time"],
        "holding_minutes": PRIMARY_RULE["holding_minutes"],
        "signal_magnitude_threshold": None,
        "volume_filter": "off",
        "volatility_filter": "off",
        "OI_filter": "off",
        "options_fast_lead_filter": "off",
        "VIX_filter": "off",
        "summary": summary,
        "structural_gate_pass": passed,
        "structural_gate_failures": failures,
    }

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
        "external_sources": EXTERNAL_SOURCES,
        "hypothesis": HYPOTHESIS,
        "feature": FEATURE,
        "primary_rule": PRIMARY_RULE,
        "execution": EXECUTION,
        "structural_gate": STRUCTURAL_GATE,
        "formulations": [formulation],
        "structural_gate_passes": (
            [formulation["formulation_id"]] if passed else []
        ),
        "decision": (
            "STRUCTURAL_PASS_REQUIRES_SEPARATE_OPTIONS_PROTOCOL"
            if passed else "REJECTED_NO_CANDIDATE_FREEZE"
        ),
        "decision_rule": DECISION_RULE,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run frozen external opening-to-closing intraday momentum screen"
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=Path("data/independent_short_swing_development_events_v2.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/independent_short_swing_external_open_close_intraday_momentum_findings.json"
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
