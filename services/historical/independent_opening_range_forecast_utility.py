"""Evaluate the fixed opening-range forecast against a median baseline."""
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
from services.historical.independent_opening_range_forecast_utility_protocol import (
    BASELINE,
    BOOTSTRAP,
    FOLDS,
    GUARDRAILS,
    MODEL,
    PREDICTOR,
    PROTOCOL_VERSION,
    SOURCE,
    TARGET,
    UTILITY_GATE,
)
from services.historical.independent_older_market_structure_findings import (
    _build_sessions,
    _validate_market,
)

RESEARCH_TYPE = "NIFTY_BREEZE_OPENING_RANGE_FORECAST_UTILITY_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_sessions(frame: pd.DataFrame) -> pd.DataFrame:
    sessions = _build_sessions(frame).copy()
    sessions["year"] = sessions["date"].str[:4].astype(int)
    usable = sessions[["date", "year", PREDICTOR, TARGET]].dropna().copy()
    if len(sessions) < int(UTILITY_GATE["minimum_complete_sessions"]):
        raise ValueError("source has fewer than 600 complete sessions")
    if set(usable["year"].unique()) != {2022, 2023, 2024}:
        raise ValueError("usable sessions must span 2022, 2023, and 2024")
    return usable.reset_index(drop=True)


def _bootstrap_improvement(oos: pd.DataFrame) -> list[float]:
    values = oos["error_improvement_bps"].to_numpy(dtype=float)
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


def _evaluate_sessions(sessions: pd.DataFrame) -> dict[str, Any]:
    fold_results: list[dict[str, Any]] = []
    oos_parts: list[pd.DataFrame] = []

    for fold in FOLDS:
        train = sessions.loc[sessions["year"].isin(fold["train_years"])].copy()
        test = sessions.loc[sessions["year"] == int(fold["test_year"])].copy()
        if train.empty or test.empty:
            raise ValueError(f"empty train/test data for fold {fold['name']}")

        beta = _fit_log_ols(train, [PREDICTOR])
        target = test[TARGET].to_numpy(dtype=float)
        model_forecast = _predict_log_ols(test, [PREDICTOR], beta)
        baseline_value = float(train[TARGET].median())
        baseline_forecast = np.full(len(test), baseline_value, dtype=float)

        model_mae = _mae(target, model_forecast)
        baseline_mae = _mae(target, baseline_forecast)
        model_spearman = _spearman(model_forecast, target)

        fold_results.append(
            {
                "name": fold["name"],
                "train_years": list(fold["train_years"]),
                "test_year": int(fold["test_year"]),
                "train_sessions": int(len(train)),
                "test_sessions": int(len(test)),
                "model_coefficients": [float(x) for x in beta],
                "baseline_median_bps": baseline_value,
                "model_mae_bps": model_mae,
                "baseline_mae_bps": baseline_mae,
                "skill": float(1.0 - model_mae / baseline_mae),
                "model_mae_lower": bool(model_mae < baseline_mae),
                "model_spearman": model_spearman,
            }
        )

        scored = test[["date", "year", TARGET]].copy()
        scored["model_forecast_bps"] = model_forecast
        scored["baseline_forecast_bps"] = baseline_forecast
        scored["error_improvement_bps"] = (
            np.abs(target - baseline_forecast)
            - np.abs(target - model_forecast)
        )
        oos_parts.append(scored)

    oos = pd.concat(oos_parts, ignore_index=True)
    target = oos[TARGET].to_numpy(dtype=float)
    model_forecast = oos["model_forecast_bps"].to_numpy(dtype=float)
    baseline_forecast = oos["baseline_forecast_bps"].to_numpy(dtype=float)

    model_mae = _mae(target, model_forecast)
    baseline_mae = _mae(target, baseline_forecast)
    ci = _bootstrap_improvement(oos)

    failures: list[str] = []
    if not all(row["model_mae_lower"] for row in fold_results):
        failures.append("model_mae_not_lower_in_both_folds")
    if model_mae >= baseline_mae:
        failures.append("model_pooled_mae_not_lower")
    if not all(row["model_spearman"] > 0.0 for row in fold_results):
        failures.append("model_spearman_not_positive_in_both_folds")
    if ci[0] <= 0.0:
        failures.append("bootstrap_error_improvement_lower_bound_not_positive")

    return {
        "fold_results": fold_results,
        "pooled_oos": {
            "sessions": int(len(oos)),
            "model_mae_bps": model_mae,
            "baseline_mae_bps": baseline_mae,
            "mae_improvement_bps": baseline_mae - model_mae,
            "skill": float(1.0 - model_mae / baseline_mae),
            "model_spearman": _spearman(model_forecast, target),
            "bootstrap_error_improvement_95pct": ci,
        },
        "utility_gate": {
            "passed": not failures,
            "failures": failures,
        },
    }


def analyze_market_file(path: Path) -> dict[str, Any]:
    digest = _sha256(path)
    if digest != SOURCE["market_artifact_sha256"]:
        raise ValueError("source market artifact SHA does not match frozen protocol")

    payload = json.loads(path.read_text(encoding="utf-8"))
    frame = _validate_market(payload)
    sessions = _prepare_sessions(frame)
    evaluation = _evaluate_sessions(sessions)
    passed = bool(evaluation["utility_gate"]["passed"])

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "source_market_sha256": digest,
        "source_complete_sessions": int(frame["date"].nunique()),
        "usable_sessions": int(len(sessions)),
        "model": MODEL,
        "baseline": BASELINE,
        **evaluation,
        "decision": (
            "OPENING_RANGE_FORECAST_BEATS_UNCONDITIONAL_MEDIAN_BASELINE"
            if passed
            else "OPENING_RANGE_FORECAST_UTILITY_NOT_STABLE_VS_MEDIAN_BASELINE"
        ),
        "interpretation_policy": (
            "Historical forecast-utility characterization only; not a trading "
            "rule, threshold, or blind validation."
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate opening-range forecast versus median baseline"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_opening_range_forecast_utility.json"),
    )
    args = parser.parse_args()
    report = analyze_market_file(args.market)
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
                "pooled_oos": report["pooled_oos"],
                "utility_gate": report["utility_gate"],
                "decision": report["decision"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
