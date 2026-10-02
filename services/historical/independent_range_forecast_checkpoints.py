"""Evaluate frozen intraday range forecast checkpoints on Breeze history."""
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
from services.historical.independent_older_market_structure_findings import (
    _validate_market,
)
from services.historical.independent_range_forecast_checkpoints_protocol import (
    BASELINE,
    BOOTSTRAP,
    CHECKPOINT_GATE,
    CHECKPOINTS_MINUTES,
    GUARDRAILS,
    INTERPRETATION,
    MODEL,
    PROTOCOL_VERSION,
    TRAINING_SOURCE,
    TRANSFER_SOURCE,
)

RESEARCH_TYPE = "NIFTY_BREEZE_RANGE_FORECAST_CHECKPOINTS_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_sessions(
    bars: pd.DataFrame,
    checkpoint_minutes: int,
) -> pd.DataFrame:
    if checkpoint_minutes not in CHECKPOINTS_MINUTES:
        raise ValueError("checkpoint not in frozen protocol")
    if checkpoint_minutes % 5 != 0:
        raise ValueError("checkpoint must align to 5-minute bars")

    first_bar_count = checkpoint_minutes // 5
    rows: list[dict[str, Any]] = []

    for day, group in bars.groupby("date", sort=True):
        group = group.sort_values("timestamp").reset_index(drop=True)
        if len(group) != int(TRANSFER_SOURCE["bars_per_session"]):
            raise ValueError(f"{day} is not an exact 75-bar session")
        if first_bar_count <= 0 or first_bar_count >= len(group):
            raise ValueError("checkpoint leaves no predictor or target bars")

        session_open = float(group.iloc[0]["open"])
        observed = group.iloc[:first_bar_count]
        remaining = group.iloc[first_bar_count:]

        predictor = (
            float(observed["high"].max()) - float(observed["low"].min())
        ) / session_open * 10000.0
        target = (
            float(remaining["high"].max()) - float(remaining["low"].min())
        ) / session_open * 10000.0

        rows.append(
            {
                "date": str(day),
                "year": int(str(day)[:4]),
                "predictor_bps": predictor,
                "target_bps": target,
            }
        )

    result = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    if result.empty:
        raise ValueError("checkpoint session frame is empty")
    return result


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


def _fit_checkpoint(training: pd.DataFrame) -> tuple[np.ndarray, float]:
    model_frame = training.rename(
        columns={"predictor_bps": "x", "target_bps": "y"}
    )
    original_target = MODEL.get("target_name")
    # Reuse the frozen OLS implementation by renaming into its expected columns.
    model_frame["post_09_40_remaining_session_high_low_range_bps"] = model_frame["y"]
    model_frame["first_30m_high_low_range_bps"] = model_frame["x"]
    beta = _fit_log_ols(
        model_frame,
        ["first_30m_high_low_range_bps"],
    )
    baseline = float(model_frame["post_09_40_remaining_session_high_low_range_bps"].median())
    _ = original_target
    return beta, baseline


def _predict_checkpoint(frame: pd.DataFrame, beta: np.ndarray) -> np.ndarray:
    model_frame = frame.copy()
    model_frame["first_30m_high_low_range_bps"] = model_frame["predictor_bps"]
    return _predict_log_ols(
        model_frame,
        ["first_30m_high_low_range_bps"],
        beta,
    )


def _score_checkpoint(
    transfer: pd.DataFrame,
    beta: np.ndarray,
    baseline_value: float,
) -> dict[str, Any]:
    scored = transfer.copy()
    target = scored["target_bps"].to_numpy(dtype=float)
    model_forecast = _predict_checkpoint(scored, beta)
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
        y = chunk["target_bps"].to_numpy(dtype=float)
        model = chunk["model_forecast_bps"].to_numpy(dtype=float)
        baseline = chunk["baseline_forecast_bps"].to_numpy(dtype=float)
        model_mae = _mae(y, model)
        baseline_mae = _mae(y, baseline)
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
        "checkpoint_gate": {
            "passed": not failures,
            "failures": failures,
        },
    }


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

    checkpoint_results: dict[str, Any] = {}
    stable: list[int] = []

    for checkpoint in CHECKPOINTS_MINUTES:
        train = _checkpoint_sessions(training_bars, checkpoint)
        transfer = _checkpoint_sessions(transfer_bars, checkpoint)
        beta, baseline = _fit_checkpoint(train)
        scored = _score_checkpoint(transfer, beta, baseline)
        passed = bool(scored["checkpoint_gate"]["passed"])
        if passed:
            stable.append(checkpoint)

        checkpoint_results[str(checkpoint)] = {
            "checkpoint_minutes": checkpoint,
            "training_sessions": int(len(train)),
            "transfer_sessions": int(len(transfer)),
            "fixed_model_coefficients": [float(x) for x in beta],
            "fixed_training_median_baseline_bps": baseline,
            **scored,
        }

    earliest = min(stable) if stable else None

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
        "checkpoints_minutes": CHECKPOINTS_MINUTES,
        "checkpoint_results": checkpoint_results,
        "stable_checkpoints_minutes": stable,
        "earliest_stable_checkpoint_minutes": earliest,
        "decision": (
            f"EARLIEST_STABLE_RANGE_FORECAST_CHECKPOINT_{earliest}M"
            if earliest is not None
            else "NO_PREDECLARED_CHECKPOINT_HAS_STABLE_FORECAST_UTILITY"
        ),
        "interpretation": INTERPRETATION,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen NIFTY intraday range forecast checkpoints"
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
        default=Path("data/independent_range_forecast_checkpoints.json"),
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
                "stable_checkpoints_minutes": report["stable_checkpoints_minutes"],
                "earliest_stable_checkpoint_minutes": report[
                    "earliest_stable_checkpoint_minutes"
                ],
                "decision": report["decision"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
