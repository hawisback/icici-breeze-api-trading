"""Locked evaluator for the future NIFTY magnitude forecasting project.

The evaluator can be unit-tested on synthetic data today, but real development
scoring is hard-locked behind the frozen prospective magnitude replication
passing. It never collects market data and it contains no trading logic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_magnitude_forecasting_design_protocol import (
    ACTIVATION,
    BOOTSTRAP,
    DATA_POLICY,
    DEVELOPMENT_GATE,
    GUARDRAILS,
    MODEL,
    OUT_OF_SAMPLE_EVALUATION,
    PREDICTOR,
    PROTOCOL_VERSION,
    TARGET,
)

RESEARCH_TYPE = "NIFTY_MAGNITUDE_FORECASTING_DEVELOPMENT_FINDINGS_V1"
EVENT_RESEARCH_TYPE = "NIFTY_MAGNITUDE_FORECASTING_DEVELOPMENT_EVENTS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_activation(prospective_findings: dict[str, Any]) -> None:
    if prospective_findings.get("protocol_version") != ACTIVATION[
        "requires_completed_prospective_protocol"
    ]:
        raise RuntimeError(
            "forecasting development is locked: prospective protocol mismatch"
        )
    if (prospective_findings.get("replication_gate") or {}).get("passed") is not True:
        raise RuntimeError(
            "forecasting development is locked: prospective gate has not passed"
        )
    if prospective_findings.get("decision") != ACTIVATION[
        "requires_pass_decision"
    ]:
        raise RuntimeError(
            "forecasting development is locked: required prospective pass "
            "decision is absent"
        )


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("Spearman inputs must have equal length >= 2")
    rx = pd.Series(x).rank(method="average").to_numpy(dtype=float)
    ry = pd.Series(y).rank(method="average").to_numpy(dtype=float)
    if float(np.std(rx)) == 0.0 or float(np.std(ry)) == 0.0:
        raise ValueError("Spearman input has zero rank variance")
    return float(np.corrcoef(rx, ry)[0, 1])


def _chronological_blocks(session_dates: list[str]) -> dict[str, list[str]]:
    dates = sorted(session_dates)
    n = len(dates)
    blocks = int(OUT_OF_SAMPLE_EVALUATION["blocks"])
    base, remainder = divmod(n, blocks)
    sizes = [base + (1 if i < remainder else 0) for i in range(blocks)]
    if min(sizes) < int(OUT_OF_SAMPLE_EVALUATION["minimum_sessions_per_block"]):
        raise ValueError(
            "development sample has too few sessions per chronological block"
        )
    result: dict[str, list[str]] = {}
    cursor = 0
    for i, size in enumerate(sizes, start=1):
        result[f"block{i}"] = dates[cursor : cursor + size]
        cursor += size
    if cursor != n:
        raise AssertionError("chronological blocks did not consume all sessions")
    return result


def _fit_log_linear_ols(
    predictor: np.ndarray,
    target: np.ndarray,
) -> tuple[float, float]:
    if len(predictor) != len(target) or len(predictor) < 2:
        raise ValueError("OLS inputs must have equal length >= 2")
    if not np.isfinite(predictor).all() or not np.isfinite(target).all():
        raise ValueError("OLS inputs must be finite")
    if (predictor < 0.0).any() or (target < 0.0).any():
        raise ValueError("magnitude predictor and target must be nonnegative")

    x = np.log1p(predictor.astype(float))
    y = np.log1p(target.astype(float))
    x_mean = float(np.mean(x))
    y_mean = float(np.mean(y))
    denominator = float(np.sum((x - x_mean) ** 2))
    if denominator <= 0.0:
        raise ValueError("training predictor has zero variance")
    slope = float(np.sum((x - x_mean) * (y - y_mean)) / denominator)
    intercept = float(y_mean - slope * x_mean)
    return intercept, slope


def _predict_log_linear(
    predictor: np.ndarray,
    intercept: float,
    slope: float,
) -> np.ndarray:
    if not np.isfinite(predictor).all() or (predictor < 0.0).any():
        raise ValueError("prediction inputs must be finite and nonnegative")
    raw = np.expm1(intercept + slope * np.log1p(predictor.astype(float)))
    return np.maximum(0.0, raw)


def _mae(target: np.ndarray, forecast: np.ndarray) -> float:
    return float(np.mean(np.abs(target - forecast)))


def _validate_development_payload(payload: dict[str, Any]) -> pd.DataFrame:
    if payload.get("research_type") != EVENT_RESEARCH_TYPE:
        raise ValueError("development event research_type mismatch")
    if payload.get("design_protocol_version") != PROTOCOL_VERSION:
        raise ValueError("development event design protocol mismatch")
    if payload.get("provider") != DATA_POLICY["provider"]:
        raise ValueError("forecast development must remain BREEZE-only")
    if payload.get("genuinely_new_development_data") is not True:
        raise ValueError("development data is not marked genuinely new")
    if payload.get("source_protocol_frozen_before_collection") is not True:
        raise ValueError("source protocol was not frozen before collection")
    if not str(payload.get("source_protocol_version") or "").strip():
        raise ValueError("source protocol version is missing")
    if not str(payload.get("source_market_artifact_sha256") or "").strip():
        raise ValueError("source market artifact SHA is missing")

    source_labels = set(payload.get("source_labels") or [])
    forbidden = set(DATA_POLICY["forbidden_scoring_sources"])
    overlap = sorted(source_labels & forbidden)
    if overlap:
        raise ValueError(
            "development scoring source overlaps forbidden inspected samples: "
            + ", ".join(overlap)
        )

    frame = pd.DataFrame(payload.get("events") or []).copy()
    required = {"date", PREDICTOR["name"], TARGET["name"]}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"development events missing columns: {missing}")
    if frame.empty:
        raise ValueError("development events are empty")

    frame["date"] = frame["date"].astype(str)
    frame[PREDICTOR["name"]] = pd.to_numeric(
        frame[PREDICTOR["name"]], errors="raise"
    )
    frame[TARGET["name"]] = pd.to_numeric(
        frame[TARGET["name"]], errors="raise"
    )
    numeric = frame[[PREDICTOR["name"], TARGET["name"]]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise ValueError("development events contain non-finite values")
    if (numeric < 0.0).any():
        raise ValueError("development magnitude values must be nonnegative")

    sessions = sorted(frame["date"].unique().tolist())
    if len(sessions) < int(OUT_OF_SAMPLE_EVALUATION["minimum_complete_sessions"]):
        raise ValueError("development sample has fewer than 80 complete sessions")
    if int(payload.get("sessions", -1)) != len(sessions):
        raise ValueError("development session count metadata mismatch")
    if int(payload.get("scorable_events", -1)) != len(frame):
        raise ValueError("development event count metadata mismatch")
    return frame.sort_values(["date"]).reset_index(drop=True)


def _evaluate_frame(frame: pd.DataFrame) -> dict[str, Any]:
    x_name = str(PREDICTOR["name"])
    y_name = str(TARGET["name"])
    sessions = sorted(frame["date"].unique().tolist())
    blocks = _chronological_blocks(sessions)
    block_by_day = {
        day: block for block, days in blocks.items() for day in days
    }
    frame = frame.copy()
    frame["block"] = frame["date"].map(block_by_day)
    if frame["block"].isna().any():
        raise AssertionError("event could not be assigned to chronological block")

    fold_results: list[dict[str, Any]] = []
    oos_rows: list[pd.DataFrame] = []

    for fold_index, fold in enumerate(
        OUT_OF_SAMPLE_EVALUATION["folds"], start=1
    ):
        train_blocks = list(fold["train_blocks"])
        test_block = str(fold["test_block"])
        train = frame.loc[frame["block"].isin(train_blocks)].copy()
        test = frame.loc[frame["block"] == test_block].copy()
        if train.empty or test.empty:
            raise ValueError("empty train or test fold")

        train_x = train[x_name].to_numpy(dtype=float)
        train_y = train[y_name].to_numpy(dtype=float)
        test_x = test[x_name].to_numpy(dtype=float)
        test_y = test[y_name].to_numpy(dtype=float)

        intercept, slope = _fit_log_linear_ols(train_x, train_y)
        model_forecast = _predict_log_linear(test_x, intercept, slope)
        baseline_value = float(np.median(train_y))
        baseline_forecast = np.full(len(test_y), baseline_value, dtype=float)

        model_mae = _mae(test_y, model_forecast)
        baseline_mae = _mae(test_y, baseline_forecast)
        if baseline_mae <= 0.0:
            raise ValueError("baseline MAE must be positive")
        skill = float(1.0 - model_mae / baseline_mae)
        spearman = _spearman(model_forecast, test_y)

        fold_results.append(
            {
                "fold": fold_index,
                "train_blocks": train_blocks,
                "test_block": test_block,
                "train_sessions": int(train["date"].nunique()),
                "test_sessions": int(test["date"].nunique()),
                "train_events": int(len(train)),
                "test_events": int(len(test)),
                "model": {
                    "intercept": intercept,
                    "slope": slope,
                },
                "baseline_training_median_bps": baseline_value,
                "model_mae_bps": model_mae,
                "baseline_mae_bps": baseline_mae,
                "mae_skill": skill,
                "spearman_forecast_vs_target": spearman,
            }
        )

        scored = test[["date", y_name]].copy()
        scored["model_forecast"] = model_forecast
        scored["baseline_forecast"] = baseline_forecast
        scored["error_improvement"] = (
            np.abs(test_y - baseline_forecast)
            - np.abs(test_y - model_forecast)
        )
        oos_rows.append(scored)

    oos = pd.concat(oos_rows, ignore_index=True)
    target = oos[y_name].to_numpy(dtype=float)
    model_forecast = oos["model_forecast"].to_numpy(dtype=float)
    baseline_forecast = oos["baseline_forecast"].to_numpy(dtype=float)
    model_mae = _mae(target, model_forecast)
    baseline_mae = _mae(target, baseline_forecast)
    pooled_skill = float(1.0 - model_mae / baseline_mae)
    pooled_spearman = _spearman(model_forecast, target)

    by_session = {
        day: group["error_improvement"].to_numpy(dtype=float)
        for day, group in oos.groupby("date", sort=True)
    }
    session_dates = sorted(by_session)
    rng = np.random.default_rng(int(BOOTSTRAP["seed"]))
    draws = np.empty(int(BOOTSTRAP["draws"]), dtype=float)
    for i in range(len(draws)):
        sampled = rng.choice(
            session_dates,
            size=len(session_dates),
            replace=True,
        )
        values = np.concatenate([by_session[str(day)] for day in sampled])
        draws[i] = float(np.mean(values))
    bootstrap_interval = [
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    ]

    failures: list[str] = []
    if model_mae >= baseline_mae:
        failures.append("pooled_model_mae_not_lower_than_baseline")
    if any(
        fold["model_mae_bps"] >= fold["baseline_mae_bps"]
        for fold in fold_results
    ):
        failures.append("model_mae_not_lower_in_all_three_folds")
    if pooled_spearman <= 0.0:
        failures.append("pooled_oos_spearman_not_positive")
    if bootstrap_interval[0] <= 0.0:
        failures.append("bootstrap_error_improvement_lower_bound_not_positive")

    return {
        "chronological_blocks": blocks,
        "fold_results": fold_results,
        "pooled_oos": {
            "sessions": int(oos["date"].nunique()),
            "events": int(len(oos)),
            "model_mae_bps": model_mae,
            "baseline_mae_bps": baseline_mae,
            "mae_skill": pooled_skill,
            "spearman_forecast_vs_target": pooled_spearman,
            "session_cluster_bootstrap_error_improvement_95pct": (
                bootstrap_interval
            ),
        },
        "development_gate": {
            "passed": not failures,
            "failures": failures,
        },
    }


def evaluate_payload(
    payload: dict[str, Any],
    prospective_findings: dict[str, Any],
) -> dict[str, Any]:
    assert_activation(prospective_findings)
    frame = _validate_development_payload(payload)
    evaluation = _evaluate_frame(frame)
    passed = bool(evaluation["development_gate"]["passed"])
    return {
        "research_type": RESEARCH_TYPE,
        "design_protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "provider": DATA_POLICY["provider"],
        "source_protocol_version": payload["source_protocol_version"],
        "source_market_artifact_sha256": payload[
            "source_market_artifact_sha256"
        ],
        "sessions": int(payload["sessions"]),
        "scorable_events": int(payload["scorable_events"]),
        "model": MODEL,
        **evaluation,
        "decision": (
            "FORECASTING_MODEL_DEVELOPMENT_PASSED_BUT_NO_TRADING_PROMOTION"
            if passed
            else "FORECASTING_MODEL_DEVELOPMENT_FAILED_NO_RESCUE_ON_SAME_SAMPLE"
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate locked NIFTY magnitude forecasting development"
    )
    parser.add_argument("--prospective-findings", type=Path, required=True)
    parser.add_argument("--development-events", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_magnitude_forecasting_findings.json"),
    )
    args = parser.parse_args()

    prospective = json.loads(
        args.prospective_findings.read_text(encoding="utf-8")
    )
    # Deliberately check activation before opening the development artifact.
    assert_activation(prospective)

    payload = json.loads(args.development_events.read_text(encoding="utf-8"))
    report = evaluate_payload(payload, prospective)
    report["development_events_artifact_sha256"] = _sha256(
        args.development_events
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "design_protocol_version": PROTOCOL_VERSION,
                "development_events_artifact_sha256": report[
                    "development_events_artifact_sha256"
                ],
                "pooled_oos": report["pooled_oos"],
                "development_gate": report["development_gate"],
                "decision": report["decision"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
