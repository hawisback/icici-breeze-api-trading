"""Evaluate fixed 5-minute range forecast calibration across frozen training quartiles."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_5m_range_calibration_protocol import (
    BINS,
    CHECKPOINT_MINUTES,
    GUARDRAILS,
    MODEL,
    MONOTONICITY_GATE,
    PROTOCOL_VERSION,
    TRAINING_SOURCE,
    TRANSFER_SOURCE,
)
from services.historical.independent_market_structure_atlas import (
    _read_breeze_rows,
    _validate_and_prepare_sessions,
)
from services.historical.independent_older_market_structure_findings import (
    _validate_market,
)
from services.historical.independent_range_forecast_checkpoints import (
    _fit_checkpoint,
    _mae,
    _predict_checkpoint,
)
from services.historical.independent_ultra_early_range_forecast import (
    _early_checkpoint_sessions,
)

RESEARCH_TYPE = "NIFTY_BREEZE_5M_RANGE_CALIBRATION_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _training_quartile_cutpoints(training: pd.DataFrame) -> list[float]:
    values = training["predictor_bps"].to_numpy(dtype=float)
    q25, q50, q75 = np.quantile(values, [0.25, 0.50, 0.75])
    if not (q25 < q50 < q75):
        raise ValueError("training predictor quartile cut points are not strictly increasing")
    return [float(q25), float(q50), float(q75)]


def _assign_frozen_quartiles(
    frame: pd.DataFrame,
    cutpoints: list[float],
) -> pd.DataFrame:
    q25, q50, q75 = cutpoints
    result = frame.copy()
    result["quartile"] = pd.cut(
        result["predictor_bps"],
        bins=[-np.inf, q25, q50, q75, np.inf],
        labels=BINS,
        include_lowest=True,
        right=True,
    )
    if result["quartile"].isna().any():
        raise ValueError("frozen quartile assignment produced missing values")
    return result


def _profile(
    frame: pd.DataFrame,
    *,
    model_forecast: np.ndarray,
    baseline_value: float,
) -> dict[str, Any]:
    scored = frame.copy()
    scored["model_forecast_bps"] = model_forecast
    scored["baseline_forecast_bps"] = baseline_value

    result: dict[str, Any] = {}
    for label in BINS:
        chunk = scored.loc[scored["quartile"] == label].copy()
        if chunk.empty:
            result[label] = {
                "sessions": 0,
            }
            continue
        target = chunk["target_bps"].to_numpy(dtype=float)
        model = chunk["model_forecast_bps"].to_numpy(dtype=float)
        baseline = chunk["baseline_forecast_bps"].to_numpy(dtype=float)
        model_mae = _mae(target, model)
        baseline_mae = _mae(target, baseline)
        result[label] = {
            "sessions": int(len(chunk)),
            "predictor_mean_bps": float(chunk["predictor_bps"].mean()),
            "target_mean_bps": float(chunk["target_bps"].mean()),
            "target_median_bps": float(chunk["target_bps"].median()),
            "forecast_mean_bps": float(chunk["model_forecast_bps"].mean()),
            "model_mae_bps": model_mae,
            "fixed_training_median_baseline_mae_bps": baseline_mae,
            "skill": float(1.0 - model_mae / baseline_mae),
            "mean_signed_error_bps": float(np.mean(model - target)),
        }
    return result


def _strictly_increasing(values: list[float]) -> bool:
    return all(later > earlier for earlier, later in zip(values, values[1:]))


def analyze_sources(training_market: Path, transfer_db: Path) -> dict[str, Any]:
    training_sha = _sha256(training_market)
    if training_sha != TRAINING_SOURCE["market_artifact_sha256"]:
        raise ValueError("training market SHA does not match frozen protocol")

    transfer_sha = _sha256(transfer_db)
    if transfer_sha != TRANSFER_SOURCE["historical_database_sha256"]:
        raise ValueError("transfer database SHA does not match frozen protocol")

    training_payload = json.loads(training_market.read_text(encoding="utf-8"))
    training_bars = _validate_market(training_payload)
    if int(training_bars["date"].nunique()) != int(
        TRAINING_SOURCE["expected_complete_sessions"]
    ):
        raise ValueError("training complete-session count changed")

    raw = _read_breeze_rows(transfer_db)
    transfer_bars, transfer_sessions, rejected = _validate_and_prepare_sessions(raw)
    if len(transfer_sessions) < int(TRANSFER_SOURCE["minimum_complete_sessions"]):
        raise ValueError("transfer sample has fewer than 400 complete sessions")

    training = _early_checkpoint_sessions(training_bars, CHECKPOINT_MINUTES)
    transfer = _early_checkpoint_sessions(transfer_bars, CHECKPOINT_MINUTES)

    beta, baseline = _fit_checkpoint(training)
    cutpoints = _training_quartile_cutpoints(training)
    assigned = _assign_frozen_quartiles(transfer, cutpoints)
    model_forecast = _predict_checkpoint(assigned, beta)

    pooled_profile = _profile(
        assigned,
        model_forecast=model_forecast,
        baseline_value=baseline,
    )

    yearly_profiles: dict[str, Any] = {}
    for year in (2025, 2026):
        chunk = assigned.loc[assigned["year"] == year].copy()
        forecast = _predict_checkpoint(chunk, beta)
        yearly_profiles[str(year)] = _profile(
            chunk,
            model_forecast=forecast,
            baseline_value=baseline,
        )

    pooled_means = [
        float(pooled_profile[label]["target_mean_bps"]) for label in BINS
    ]
    pooled_medians = [
        float(pooled_profile[label]["target_median_bps"]) for label in BINS
    ]
    year_means = {
        year: [
            float(yearly_profiles[year][label]["target_mean_bps"])
            for label in BINS
        ]
        for year in ("2025", "2026")
    }

    all_nonempty = all(
        int(yearly_profiles[year][label]["sessions"]) > 0
        for year in ("2025", "2026")
        for label in BINS
    )
    failures: list[str] = []
    if not _strictly_increasing(pooled_means):
        failures.append("pooled_target_means_not_strictly_increasing")
    if not _strictly_increasing(pooled_medians):
        failures.append("pooled_target_medians_not_strictly_increasing")
    if not _strictly_increasing(year_means["2025"]):
        failures.append("2025_target_means_not_strictly_increasing")
    if not _strictly_increasing(year_means["2026"]):
        failures.append("2026_target_means_not_strictly_increasing")
    if not all_nonempty:
        failures.append("frozen_quartile_empty_in_transfer_year")

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "training_market_sha256": training_sha,
        "transfer_database_sha256": transfer_sha,
        "training_complete_sessions": int(training_bars["date"].nunique()),
        "transfer_complete_sessions": int(len(transfer_sessions)),
        "transfer_rejected_sessions": int(len(rejected)),
        "transfer_rejections": rejected,
        "checkpoint_minutes": CHECKPOINT_MINUTES,
        "training_quartile_cutpoints_bps": cutpoints,
        "fixed_model": {
            **MODEL,
            "coefficients": [float(x) for x in beta],
        },
        "fixed_training_median_baseline_bps": baseline,
        "pooled_quartile_profile": pooled_profile,
        "calendar_year_quartile_profiles": yearly_profiles,
        "monotonicity_gate": {
            "passed": not failures,
            "failures": failures,
            "all_frozen_quartiles_nonempty_in_2025_and_2026": all_nonempty,
        },
        "decision": (
            "FIVE_MINUTE_RANGE_RELATIONSHIP_IS_BROAD_AND_MONOTONE"
            if not failures
            else "FIVE_MINUTE_RANGE_RELATIONSHIP_NOT_MONOTONE_ACROSS_FROZEN_QUARTILES"
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate 5m range forecast calibration across frozen quartiles"
    )
    parser.add_argument("--training-market", type=Path, required=True)
    parser.add_argument(
        "--transfer-db",
        type=Path,
        default=Path("data/market/historical.db"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_5m_range_calibration.json"),
    )
    args = parser.parse_args()
    report = analyze_sources(args.training_market, args.transfer_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "training_quartile_cutpoints_bps": report[
                    "training_quartile_cutpoints_bps"
                ],
                "monotonicity_gate": report["monotonicity_gate"],
                "decision": report["decision"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
