"""Development-only 3/6-bar NIFTY futures sequence atlas.

The atlas is descriptive. It uses fixed sequence measurements and fixed
10/15/30/60-minute outcomes from the next bar open. It does not define a
candidate, optimize thresholds, calculate P&L, or inspect blind datasets.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DEVELOPMENT_FUTURES_SHA256 = "3724809a5ddc1c0dce05990b6b405442bb299406f13af585b589a7b522cdc21e"
WINDOWS = (3, 6)
HORIZONS_MINUTES = (10, 15, 30, 60)


def _spearman(frame: pd.DataFrame, left: str, right: str) -> float:
    return float(frame[left].corr(frame[right], method="spearman"))


def _rolling_sum_by_date(frame: pd.DataFrame, column: str, window: int) -> pd.Series:
    return (
        frame[column]
        .groupby(frame["date"])
        .rolling(window)
        .sum()
        .reset_index(level=0, drop=True)
    )


def build_sequence_frame(payload: dict[str, Any]) -> pd.DataFrame:
    sessions = list(payload["session_dates"])
    if len(sessions) != 80:
        raise ValueError(f"sequence atlas requires 80 development sessions, got {len(sessions)}")
    frame = pd.DataFrame(payload["canonical_market_rows"]).copy()
    if len(frame) != 6000:
        raise ValueError(f"expected 6000 canonical futures rows, got {len(frame)}")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["date"] = frame["timestamp"].dt.date.astype(str)
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame["block"] = frame["date"].map({day: index // 10 + 1 for index, day in enumerate(sessions)})
    group = frame.groupby("date")
    frame["previous_close"] = group["futures_close"].shift(1)
    frame["bar_return_bps"] = (frame["futures_close"] / frame["previous_close"] - 1.0) * 10000.0

    next_open = group["futures_open"].shift(-1)
    for minutes in HORIZONS_MINUTES:
        bars = minutes // 5
        end_close = group["futures_close"].shift(-bars)
        frame[f"future_terminal_{minutes}m_bps"] = (end_close / next_open - 1.0) * 10000.0
        highs = pd.concat(
            [group["futures_high"].shift(-lag) for lag in range(1, bars + 1)], axis=1
        ).max(axis=1)
        lows = pd.concat(
            [group["futures_low"].shift(-lag) for lag in range(1, bars + 1)], axis=1
        ).min(axis=1)
        frame[f"future_max_excursion_{minutes}m_bps"] = np.maximum(
            (highs / next_open - 1.0).abs(),
            (lows / next_open - 1.0).abs(),
        ) * 10000.0

    for window in WINDOWS:
        start_open = group["futures_open"].shift(window - 1)
        net = f"net_return_{window}_bps"
        path = f"path_length_{window}_bps"
        frame[net] = (frame["futures_close"] / start_open - 1.0) * 10000.0
        frame[path] = _rolling_sum_by_date(frame.assign(_abs=frame["bar_return_bps"].abs()), "_abs", window)
        frame[f"path_efficiency_{window}"] = frame[net].abs() / frame[path].replace(0.0, np.nan)

        highs = pd.concat(
            [group["futures_high"].shift(lag) for lag in range(window)], axis=1
        ).max(axis=1)
        lows = pd.concat(
            [group["futures_low"].shift(lag) for lag in range(window)], axis=1
        ).min(axis=1)
        frame[f"range_{window}_bps"] = (highs - lows) / start_open * 10000.0
        frame[f"close_location_{window}"] = (
            2.0 * (frame["futures_close"] - lows) / (highs - lows).replace(0.0, np.nan) - 1.0
        )

        current_volume = (
            frame["futures_volume"]
            .groupby(frame["date"])
            .rolling(window)
            .mean()
            .reset_index(level=0, drop=True)
        )
        previous_volume = current_volume.groupby(frame["date"]).shift(window)
        frame[f"volume_expansion_{window}"] = np.log(current_volume / previous_volume)

        start_oi = group["futures_open_interest"].shift(window - 1)
        frame[f"oi_change_{window}_bps"] = (
            frame["futures_open_interest"] / start_oi - 1.0
        ) * 10000.0
        frame[f"price_oi_interaction_{window}"] = (
            np.sign(frame[net]) * frame[f"oi_change_{window}_bps"]
        )

        half = window // 2
        recent = _rolling_sum_by_date(
            frame.assign(_abs=frame["bar_return_bps"].abs()), "_abs", half
        )
        earlier = recent.groupby(frame["date"]).shift(half)
        frame[f"absolute_return_acceleration_{window}"] = np.log(
            (recent + 1e-6) / (earlier + 1e-6)
        )
    return frame


def build_atlas(payload: dict[str, Any]) -> dict[str, Any]:
    frame = build_sequence_frame(payload)
    features: list[dict[str, Any]] = []
    for window in WINDOWS:
        names = [
            f"net_return_{window}_bps",
            f"path_length_{window}_bps",
            f"path_efficiency_{window}",
            f"range_{window}_bps",
            f"close_location_{window}",
            f"volume_expansion_{window}",
            f"oi_change_{window}_bps",
            f"price_oi_interaction_{window}",
            f"absolute_return_acceleration_{window}",
        ]
        for feature in names:
            outcomes = []
            for minutes in HORIZONS_MINUTES:
                for kind in ("terminal", "max_excursion"):
                    outcome = f"future_{kind}_{minutes}m_bps"
                    sample = frame[[feature, outcome, "block"]].dropna()
                    block_values = [
                        _spearman(group, feature, outcome)
                        for _, group in sample.groupby("block")
                    ]
                    outcomes.append(
                        {
                            "outcome": outcome,
                            "rows": int(len(sample)),
                            "spearman": _spearman(sample, feature, outcome),
                            "positive_blocks": int(sum(value > 0 for value in block_values)),
                            "negative_blocks": int(sum(value < 0 for value in block_values)),
                            "block_spearman": block_values,
                        }
                    )
            features.append(
                {"window_bars": window, "feature": feature, "outcomes": outcomes}
            )

    return {
        "research_type": "NIFTY_FUTURES_SEQUENCE_ATLAS_V1",
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "development_source": {
            "expected_sha256": DEVELOPMENT_FUTURES_SHA256,
            "sessions": 80,
            "rows": 6000,
            "chronological_blocks": 8,
        },
        "measurement_design": {
            "sequence_windows_bars": list(WINDOWS),
            "future_horizons_minutes": list(HORIZONS_MINUTES),
            "outcome_entry": "next completed 5-minute bar open after sequence observation",
            "threshold_optimization": False,
            "pnl_optimization": False,
        },
        "features": features,
        "interpretation_guardrail": (
            "Strong movement-state relationships remain descriptive and do not "
            "restart paused target-sizing work. Directional relationships must "
            "show material magnitude as well as chronological stability before "
            "candidate freeze."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build development-only NIFTY futures sequence atlas")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    atlas = build_atlas(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(atlas, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "research_type": atlas["research_type"]}, indent=2))


if __name__ == "__main__":
    main()
