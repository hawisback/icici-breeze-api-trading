"""Evaluate frozen 5m/10m NIFTY range forecast checkpoints on Breeze history."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from services.historical.independent_market_structure_atlas import (
    _read_breeze_rows,
    _validate_and_prepare_sessions,
)
from services.historical.independent_older_market_structure_findings import (
    _validate_market,
)
from services.historical.independent_range_forecast_checkpoints import (
    _fit_checkpoint,
    _score_checkpoint,
)
from services.historical.independent_ultra_early_range_forecast_protocol import (
    CHECKPOINTS_MINUTES,
    GUARDRAILS,
    INTERPRETATION,
    PROTOCOL_VERSION,
    TRAINING_SOURCE,
    TRANSFER_SOURCE,
)

RESEARCH_TYPE = "NIFTY_BREEZE_ULTRA_EARLY_RANGE_FORECAST_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _early_checkpoint_sessions(
    bars: pd.DataFrame,
    checkpoint_minutes: int,
) -> pd.DataFrame:
    if checkpoint_minutes not in CHECKPOINTS_MINUTES:
        raise ValueError("checkpoint not in frozen ultra-early protocol")
    if checkpoint_minutes % 5 != 0:
        raise ValueError("checkpoint must align to 5-minute bars")

    observed_bars = checkpoint_minutes // 5
    rows: list[dict[str, Any]] = []

    for day, group in bars.groupby("date", sort=True):
        group = group.sort_values("timestamp").reset_index(drop=True)
        if len(group) != int(TRANSFER_SOURCE["bars_per_session"]):
            raise ValueError(f"{day} is not an exact 75-bar session")
        if observed_bars <= 0 or observed_bars >= len(group):
            raise ValueError("checkpoint leaves no predictor or target bars")

        session_open = float(group.iloc[0]["open"])
        observed = group.iloc[:observed_bars]
        remaining = group.iloc[observed_bars:]

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
        raise ValueError("ultra-early checkpoint session frame is empty")
    return result


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
        train = _early_checkpoint_sessions(training_bars, checkpoint)
        transfer = _early_checkpoint_sessions(transfer_bars, checkpoint)
        beta, baseline = _fit_checkpoint(train)
        scored = _score_checkpoint(transfer, beta, baseline)
        if bool(scored["checkpoint_gate"]["passed"]):
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
            f"EARLIEST_STABLE_ULTRA_EARLY_RANGE_FORECAST_{earliest}M"
            if earliest is not None
            else "NO_ULTRA_EARLY_CHECKPOINT_HAS_STABLE_FORECAST_UTILITY"
        ),
        "interpretation": INTERPRETATION,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen 5m/10m NIFTY range forecast checkpoints"
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
        default=Path("data/independent_ultra_early_range_forecast.json"),
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
