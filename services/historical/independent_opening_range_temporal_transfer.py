"""Apply the fixed 2022-2024 opening-range model to 2025-2026 history."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_incremental_range_forecast import (
    _fit_log_ols,
    _mae,
    _predict_log_ols,
    _spearman,
)
from services.historical.independent_market_structure_atlas import (
    _read_breeze_rows,
    _validate_and_prepare_sessions,
)
from services.historical.independent_opening_range_temporal_transfer_protocol import (
    BASELINE,
    BOOTSTRAP,
    GUARDRAILS,
    MODEL,
    PREDICTOR,
    PROTOCOL_VERSION,
    TARGET,
    TRAINING_SOURCE,
    TRANSFER_GATE,
    TRANSFER_SOURCE,
)
from services.historical.independent_older_market_structure_findings import (
    _build_sessions,
    _validate_market,
)

RESEARCH_TYPE = "NIFTY_BREEZE_OPENING_RANGE_TEMPORAL_TRANSFER_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_training(path: Path) -> tuple[pd.DataFrame, np.ndarray, float]:
    digest = _sha256(path)
    if digest != TRAINING_SOURCE["market_artifact_sha256"]:
        raise ValueError("training market artifact SHA does not match frozen protocol")
    payload = json.loads(path.read_text(encoding="utf-8"))
    frame = _validate_market(payload)
    sessions = _build_sessions(frame).copy()
    if int(sessions["date"].nunique()) != int(
        TRAINING_SOURCE["expected_complete_sessions"]
    ):
        raise ValueError("training complete-session count changed")
    usable = sessions[["date", PREDICTOR, TARGET]].dropna().copy()
    beta = _fit_log_ols(usable, [PREDICTOR])
    baseline = float(usable[TARGET].median())
    return usable.reset_index(drop=True), beta, baseline


def _prepare_transfer(path: Path) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    digest = _sha256(path)
    if digest != TRANSFER_SOURCE["historical_database_sha256"]:
        raise ValueError("transfer database SHA does not match frozen protocol")
    raw = _read_breeze_rows(path)
    _, sessions, rejected = _validate_and_prepare_sessions(raw)
    sessions = sessions.copy()
    sessions["year"] = sessions["date"].str[:4].astype(int)
    usable = sessions[["date", "year", PREDICTOR, TARGET]].dropna().copy()
    if len(usable) < int(TRANSFER_GATE["minimum_complete_sessions"]):
        raise ValueError("transfer sample has fewer than 400 complete sessions")
    if set(usable["year"].unique()) != {2025, 2026}:
        raise ValueError("transfer sample must contain both 2025 and 2026")
    return usable.reset_index(drop=True), rejected


def _bootstrap_improvement(scored: pd.DataFrame) -> list[float]:
    values = scored["error_improvement_bps"].to_numpy(dtype=float)
    rng = np.random.default_rng(int(BOOTSTRAP["seed"]))
    n = len(values)
    draws = np.empty(int(BOOTSTRAP["draws"]), dtype=float)
    for i in range(len(draws)):
        idx = rng.integers(0, n, size=n)
        draws[i] = float(np.mean(values[idx]))
    return [
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    ]


def _score_transfer(
    transfer: pd.DataFrame,
    beta: np.ndarray,
    baseline_value: float,
) -> dict[str, Any]:
    scored = transfer.copy()
    target = scored[TARGET].to_numpy(dtype=float)
    model_forecast = _predict_log_ols(scored, [PREDICTOR], beta)
    baseline_forecast = np.full(len(scored), baseline_value, dtype=float)
    scored["model_forecast_bps"] = model_forecast
    scored["baseline_forecast_bps"] = baseline_forecast
    scored["error_improvement_bps"] = (
        np.abs(target - baseline_forecast)
        - np.abs(target - model_forecast)
    )

    yearly: dict[str, Any] = {}
    for year in (2025, 2026):
        chunk = scored.loc[scored["year"] == year]
        y = chunk[TARGET].to_numpy(dtype=float)
        model = chunk["model_forecast_bps"].to_numpy(dtype=float)
        base = chunk["baseline_forecast_bps"].to_numpy(dtype=float)
        model_mae = _mae(y, model)
        baseline_mae = _mae(y, base)
        yearly[str(year)] = {
            "sessions": int(len(chunk)),
            "model_mae_bps": model_mae,
            "baseline_mae_bps": baseline_mae,
            "mae_improvement_bps": baseline_mae - model_mae,
            "skill": float(1.0 - model_mae / baseline_mae),
            "model_mae_lower": bool(model_mae < baseline_mae),
            "model_spearman": _spearman(model, y),
        }

    model_mae = _mae(target, model_forecast)
    baseline_mae = _mae(target, baseline_forecast)
    ci = _bootstrap_improvement(scored)

    failures: list[str] = []
    if not yearly["2025"]["model_mae_lower"]:
        failures.append("model_mae_not_lower_in_2025")
    if not yearly["2026"]["model_mae_lower"]:
        failures.append("model_mae_not_lower_in_2026")
    if model_mae >= baseline_mae:
        failures.append("model_pooled_mae_not_lower")
    if yearly["2025"]["model_spearman"] <= 0.0:
        failures.append("model_spearman_not_positive_in_2025")
    if yearly["2026"]["model_spearman"] <= 0.0:
        failures.append("model_spearman_not_positive_in_2026")
    if ci[0] <= 0.0:
        failures.append("bootstrap_error_improvement_lower_bound_not_positive")

    return {
        "calendar_year_results": yearly,
        "pooled_transfer": {
            "sessions": int(len(scored)),
            "model_mae_bps": model_mae,
            "baseline_mae_bps": baseline_mae,
            "mae_improvement_bps": baseline_mae - model_mae,
            "skill": float(1.0 - model_mae / baseline_mae),
            "model_spearman": _spearman(model_forecast, target),
            "bootstrap_error_improvement_95pct": ci,
        },
        "transfer_gate": {
            "passed": not failures,
            "failures": failures,
        },
    }


def analyze_sources(training_market: Path, transfer_db: Path) -> dict[str, Any]:
    training, beta, baseline = _prepare_training(training_market)
    transfer, rejected = _prepare_transfer(transfer_db)
    evaluation = _score_transfer(transfer, beta, baseline)
    passed = bool(evaluation["transfer_gate"]["passed"])

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "training_market_sha256": _sha256(training_market),
        "transfer_database_sha256": _sha256(transfer_db),
        "training_sessions": int(len(training)),
        "transfer_complete_sessions": int(len(transfer)),
        "transfer_rejected_sessions": int(len(rejected)),
        "transfer_rejections": rejected,
        "fixed_model": {
            **MODEL,
            "coefficients": [float(x) for x in beta],
        },
        "fixed_baseline": {
            **BASELINE,
            "median_target_bps": baseline,
        },
        **evaluation,
        "decision": (
            "FIXED_OLDER_OPENING_RANGE_MODEL_TRANSFERS_TO_2025_2026"
            if passed
            else "FIXED_OLDER_OPENING_RANGE_MODEL_TRANSFER_NOT_STABLE"
        ),
        "interpretation_policy": (
            "Post-discovery historical temporal transfer only. The 2025-2026 "
            "sample is not blind validation because it contributed to original "
            "relationship discovery."
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test fixed older opening-range model on 2025-2026 Breeze history"
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
        default=Path("data/independent_opening_range_temporal_transfer.json"),
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
                "calendar_year_results": report["calendar_year_results"],
                "pooled_transfer": report["pooled_transfer"],
                "transfer_gate": report["transfer_gate"],
                "decision": report["decision"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
