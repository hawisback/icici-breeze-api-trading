"""Evaluate incremental range information on the frozen 2022-2024 Breeze artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_incremental_range_forecast_protocol import (
    BOOTSTRAP,
    FOLDS,
    GUARDRAILS,
    INCREMENTAL_GATE,
    MODELS,
    OPENING_PREDICTOR,
    PERSISTENCE_PREDICTOR,
    PROTOCOL_VERSION,
    SOURCE,
    TARGET,
)
from services.historical.independent_older_market_structure_findings import (
    _build_sessions,
    _validate_market,
)

RESEARCH_TYPE = "NIFTY_BREEZE_INCREMENTAL_RANGE_FORECAST_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fit_log_ols(frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    y = np.log1p(frame[TARGET].to_numpy(dtype=float))
    columns = [np.ones(len(frame), dtype=float)]
    for feature in features:
        columns.append(np.log1p(frame[feature].to_numpy(dtype=float)))
    x = np.column_stack(columns)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("model inputs contain non-finite values")
    if len(frame) <= x.shape[1]:
        raise ValueError("too few observations for fixed OLS model")
    beta, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    if rank != x.shape[1]:
        raise ValueError("fixed OLS design matrix is rank deficient")
    return beta.astype(float)


def _predict_log_ols(
    frame: pd.DataFrame,
    features: list[str],
    beta: np.ndarray,
) -> np.ndarray:
    columns = [np.ones(len(frame), dtype=float)]
    for feature in features:
        columns.append(np.log1p(frame[feature].to_numpy(dtype=float)))
    x = np.column_stack(columns)
    forecast = np.expm1(x @ beta)
    return np.maximum(0.0, forecast.astype(float))


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx = pd.Series(x).rank(method="average").to_numpy(dtype=float)
    ry = pd.Series(y).rank(method="average").to_numpy(dtype=float)
    if len(rx) < 3 or float(np.std(rx)) == 0.0 or float(np.std(ry)) == 0.0:
        raise ValueError("invalid Spearman inputs")
    return float(np.corrcoef(rx, ry)[0, 1])


def _mae(target: np.ndarray, forecast: np.ndarray) -> float:
    return float(np.mean(np.abs(target - forecast)))


def _bootstrap_incremental_improvement(
    oos: pd.DataFrame,
) -> list[float]:
    values = oos["incremental_error_improvement_bps"].to_numpy(dtype=float)
    rng = np.random.default_rng(int(BOOTSTRAP["seed"]))
    draws = np.empty(int(BOOTSTRAP["draws"]), dtype=float)
    n = len(values)
    for i in range(len(draws)):
        idx = rng.integers(0, n, size=n)
        draws[i] = float(np.mean(values[idx]))
    return [
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    ]


def _prepare_sessions(frame: pd.DataFrame) -> pd.DataFrame:
    sessions = _build_sessions(frame).copy()
    sessions["year"] = sessions["date"].str[:4].astype(int)
    usable = sessions[
        ["date", "year", OPENING_PREDICTOR, PERSISTENCE_PREDICTOR, TARGET]
    ].dropna().copy()
    if len(sessions) < int(INCREMENTAL_GATE["minimum_complete_sessions"]):
        raise ValueError("source has fewer than 600 complete sessions")
    if set(usable["year"].unique()) != {2022, 2023, 2024}:
        raise ValueError("usable sessions must span 2022, 2023, and 2024")
    for column in (OPENING_PREDICTOR, PERSISTENCE_PREDICTOR, TARGET):
        values = usable[column].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0.0).any():
            raise ValueError(f"invalid nonnegative range values in {column}")
    return usable.reset_index(drop=True)


def _evaluate_sessions(sessions: pd.DataFrame) -> dict[str, Any]:
    fold_results: list[dict[str, Any]] = []
    oos_parts: list[pd.DataFrame] = []

    opening_features = list(MODELS["opening_only"]["features"])
    combined_features = list(MODELS["opening_plus_previous_day"]["features"])

    for fold in FOLDS:
        train = sessions.loc[sessions["year"].isin(fold["train_years"])].copy()
        test = sessions.loc[sessions["year"] == int(fold["test_year"])].copy()
        if train.empty or test.empty:
            raise ValueError(f"empty train/test data for fold {fold['name']}")

        beta_open = _fit_log_ols(train, opening_features)
        beta_combined = _fit_log_ols(train, combined_features)

        target = test[TARGET].to_numpy(dtype=float)
        forecast_open = _predict_log_ols(test, opening_features, beta_open)
        forecast_combined = _predict_log_ols(
            test, combined_features, beta_combined
        )

        mae_open = _mae(target, forecast_open)
        mae_combined = _mae(target, forecast_combined)

        fold_results.append(
            {
                "name": fold["name"],
                "train_years": list(fold["train_years"]),
                "test_year": int(fold["test_year"]),
                "train_sessions": int(len(train)),
                "test_sessions": int(len(test)),
                "opening_only_coefficients": [float(x) for x in beta_open],
                "opening_plus_previous_day_coefficients": [
                    float(x) for x in beta_combined
                ],
                "opening_only_mae_bps": mae_open,
                "opening_plus_previous_day_mae_bps": mae_combined,
                "combined_minus_opening_mae_bps": mae_combined - mae_open,
                "combined_mae_lower": bool(mae_combined < mae_open),
                "opening_only_spearman": _spearman(forecast_open, target),
                "opening_plus_previous_day_spearman": _spearman(
                    forecast_combined, target
                ),
            }
        )

        scored = test[["date", "year", TARGET]].copy()
        scored["opening_only_forecast_bps"] = forecast_open
        scored["opening_plus_previous_day_forecast_bps"] = forecast_combined
        scored["incremental_error_improvement_bps"] = (
            np.abs(target - forecast_open)
            - np.abs(target - forecast_combined)
        )
        oos_parts.append(scored)

    oos = pd.concat(oos_parts, ignore_index=True)
    target = oos[TARGET].to_numpy(dtype=float)
    opening = oos["opening_only_forecast_bps"].to_numpy(dtype=float)
    combined = oos["opening_plus_previous_day_forecast_bps"].to_numpy(dtype=float)

    pooled_open_mae = _mae(target, opening)
    pooled_combined_mae = _mae(target, combined)
    bootstrap_ci = _bootstrap_incremental_improvement(oos)

    failures: list[str] = []
    if not all(row["combined_mae_lower"] for row in fold_results):
        failures.append("combined_mae_not_lower_in_both_folds")
    if pooled_combined_mae >= pooled_open_mae:
        failures.append("combined_pooled_mae_not_lower")
    if bootstrap_ci[0] <= 0.0:
        failures.append("bootstrap_incremental_improvement_lower_bound_not_positive")

    return {
        "fold_results": fold_results,
        "pooled_oos": {
            "sessions": int(len(oos)),
            "opening_only_mae_bps": pooled_open_mae,
            "opening_plus_previous_day_mae_bps": pooled_combined_mae,
            "mae_improvement_bps": pooled_open_mae - pooled_combined_mae,
            "relative_mae_improvement": float(
                1.0 - pooled_combined_mae / pooled_open_mae
            ),
            "opening_only_spearman": _spearman(opening, target),
            "opening_plus_previous_day_spearman": _spearman(combined, target),
            "bootstrap_incremental_error_improvement_95pct": bootstrap_ci,
        },
        "incremental_gate": {
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
    passed = bool(evaluation["incremental_gate"]["passed"])

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "source_market_sha256": digest,
        "source_complete_sessions": int(frame["date"].nunique()),
        "usable_sessions_after_previous_day_lag": int(len(sessions)),
        "models": MODELS,
        **evaluation,
        "decision": (
            "PREVIOUS_DAY_RANGE_ADDS_INCREMENTAL_REMAINING_RANGE_INFORMATION"
            if passed
            else "NO_STABLE_INCREMENTAL_INFORMATION_FROM_PREVIOUS_DAY_RANGE"
        ),
        "interpretation_policy": (
            "This is historical incremental-information characterization on "
            "already inspected years, not blind validation or a trading rule."
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate incremental NIFTY range forecast information"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_incremental_range_forecast_findings.json"),
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
                "source_market_sha256": report["source_market_sha256"],
                "pooled_oos": report["pooled_oos"],
                "incremental_gate": report["incremental_gate"],
                "decision": report["decision"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
