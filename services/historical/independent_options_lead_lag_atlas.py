"""Development-only lead/lag atlas for independent NIFTY options research.

Descriptive research only: this module does not define O3, read blind results,
optimize a strategy, or calculate P&L.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DEVELOPMENT_SHA256 = "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c"


def _atm_strike(price: pd.Series, step: int = 50) -> pd.Series:
    return (np.floor(price / step + 0.5) * step).astype(int)


def _spearman(frame: pd.DataFrame, left: str, right: str) -> float:
    return float(frame[left].corr(frame[right], method="spearman"))


def _ols_coefficients(frame: pd.DataFrame) -> dict[str, float]:
    x = np.column_stack(
        [
            np.ones(len(frame)),
            frame["lead_gap_move_bps"].to_numpy(dtype=float),
            frame["fut_ret_bps"].to_numpy(dtype=float),
        ]
    )
    y = frame["future_ret_5m_bps"].to_numpy(dtype=float)
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    fitted = x @ beta
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return {
        "intercept_bps": float(beta[0]),
        "lead_gap_coefficient": float(beta[1]),
        "current_futures_return_coefficient": float(beta[2]),
        "r_squared": float(1.0 - ss_res / ss_tot) if ss_tot else 0.0,
    }


def build_atlas(payload: dict[str, Any]) -> dict[str, Any]:
    sessions = list(payload["session_dates"])
    if len(sessions) != 80:
        raise ValueError(f"lead/lag development atlas requires 80 sessions, got {len(sessions)}")

    underlying = pd.DataFrame(payload["underlying_market_rows"]).copy()
    options = pd.DataFrame(payload["option_candles"]).copy()
    underlying["timestamp"] = pd.to_datetime(underlying["timestamp"])
    options["timestamp"] = pd.to_datetime(options["timestamp"])
    underlying["date"] = underlying["timestamp"].dt.date.astype(str)
    options["date"] = options["timestamp"].dt.date.astype(str)
    underlying = underlying.sort_values("timestamp").reset_index(drop=True)

    block_by_date = {day: index // 10 + 1 for index, day in enumerate(sessions)}
    underlying["block"] = underlying["date"].map(block_by_date)
    underlying["expiry"] = underlying["date"].map(payload["contract_by_date"])
    underlying["dte"] = (
        pd.to_datetime(underlying["expiry"]) - pd.to_datetime(underlying["date"])
    ).dt.days
    underlying["atm"] = _atm_strike(underlying["futures_close"])
    underlying["prev_futures_close"] = underlying.groupby("date")["futures_close"].shift(1)
    underlying["fut_ret_bps"] = (
        underlying["futures_close"] / underlying["prev_futures_close"] - 1.0
    ) * 10000.0

    for lag in range(1, 7):
        prior = (
            underlying["futures_close"]
            if lag == 1
            else underlying.groupby("date")["futures_close"].shift(-(lag - 1))
        )
        later = underlying.groupby("date")["futures_close"].shift(-lag)
        underlying[f"future_bar_{lag}_ret_bps"] = (later / prior - 1.0) * 10000.0
    for bars in (2, 3, 6, 12):
        later = underlying.groupby("date")["futures_close"].shift(-bars)
        underlying[f"future_ret_{bars * 5}m_bps"] = (
            later / underlying["futures_close"] - 1.0
        ) * 10000.0
    underlying["future_ret_5m_bps"] = underlying["future_bar_1_ret_bps"]

    wanted = underlying[["timestamp", "date", "expiry", "atm"]]
    selected = options.merge(wanted, on=["timestamp", "date", "expiry"], how="inner")
    selected = selected[selected["strike"] == selected["atm"]].copy()
    pair = selected.pivot_table(
        index=["timestamp", "expiry", "strike"],
        columns="right",
        values="close",
        aggfunc="first",
    ).reset_index()
    if not {"CE", "PE"}.issubset(pair.columns):
        raise ValueError("ATM CE/PE pair is incomplete")
    if len(pair) != len(underlying):
        raise ValueError(f"expected one ATM pair per underlying bar, got {len(pair)}")

    pair["synthetic_forward"] = pair["strike"] + pair["CE"] - pair["PE"]
    frame = underlying.merge(
        pair[["timestamp", "synthetic_forward"]], on="timestamp", how="left"
    )
    frame["prev_synthetic_forward"] = frame.groupby("date")["synthetic_forward"].shift(1)
    frame["synthetic_return_bps"] = (
        frame["synthetic_forward"] / frame["prev_synthetic_forward"] - 1.0
    ) * 10000.0
    frame["lead_gap_move_bps"] = frame["synthetic_return_bps"] - frame["fut_ret_bps"]
    frame["aligned_next_5m_bps"] = (
        np.sign(frame["lead_gap_move_bps"]) * frame["future_ret_5m_bps"]
    )

    valid = frame.dropna(
        subset=["lead_gap_move_bps", "future_ret_5m_bps", "fut_ret_bps"]
    ).copy()
    pooled_ols = _ols_coefficients(valid)

    blocks = []
    for block, group in valid.groupby("block"):
        blocks.append(
            {
                "block": int(block),
                "rows": int(len(group)),
                "lead_gap_vs_next_5m_spearman": _spearman(
                    group, "lead_gap_move_bps", "future_ret_5m_bps"
                ),
                "mean_direction_aligned_next_5m_bps": float(
                    group["aligned_next_5m_bps"].mean()
                ),
                "direction_hit_rate": float((group["aligned_next_5m_bps"] > 0).mean()),
                **_ols_coefficients(group),
            }
        )

    horizon_profile = []
    for minutes, column in (
        (5, "future_ret_5m_bps"),
        (10, "future_ret_10m_bps"),
        (15, "future_ret_15m_bps"),
        (30, "future_ret_30m_bps"),
        (60, "future_ret_60m_bps"),
    ):
        sample = frame.dropna(subset=["lead_gap_move_bps", column])
        horizon_profile.append(
            {
                "minutes": minutes,
                "rows": int(len(sample)),
                "spearman": _spearman(sample, "lead_gap_move_bps", column),
                "positive_blocks": int(
                    sum(
                        _spearman(group, "lead_gap_move_bps", column) > 0
                        for _, group in sample.groupby("block")
                    )
                ),
            }
        )

    individual_bar_profile = []
    for lag in range(1, 7):
        column = f"future_bar_{lag}_ret_bps"
        sample = frame.dropna(subset=["lead_gap_move_bps", column])
        individual_bar_profile.append(
            {
                "future_5m_bar_lag": lag,
                "rows": int(len(sample)),
                "spearman": _spearman(sample, "lead_gap_move_bps", column),
            }
        )

    dte_summary = []
    for dte, group in valid.groupby("dte"):
        dte_summary.append(
            {
                "dte": int(dte),
                "rows": int(len(group)),
                "spearman": _spearman(
                    group, "lead_gap_move_bps", "future_ret_5m_bps"
                ),
                "mean_direction_aligned_next_5m_bps": float(
                    group["aligned_next_5m_bps"].mean()
                ),
                "direction_hit_rate": float((group["aligned_next_5m_bps"] > 0).mean()),
            }
        )

    valid["abs_gap_quartile"] = (
        pd.qcut(valid["lead_gap_move_bps"].abs(), 4, labels=False) + 1
    )
    quartiles = []
    for quartile, group in valid.groupby("abs_gap_quartile"):
        quartiles.append(
            {
                "quartile": int(quartile),
                "rows": int(len(group)),
                "mean_abs_lead_gap_bps": float(group["lead_gap_move_bps"].abs().mean()),
                "mean_direction_aligned_next_5m_bps": float(
                    group["aligned_next_5m_bps"].mean()
                ),
                "median_direction_aligned_next_5m_bps": float(
                    group["aligned_next_5m_bps"].median()
                ),
                "direction_hit_rate": float((group["aligned_next_5m_bps"] > 0).mean()),
                "positive_mean_blocks": int(
                    sum(
                        value > 0
                        for value in group.groupby("block")[
                            "aligned_next_5m_bps"
                        ].mean()
                    )
                ),
            }
        )

    return {
        "research_type": "NIFTY_OPTIONS_LEAD_LAG_ATLAS_V1",
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "development_corpus": {
            "expected_sha256": DEVELOPMENT_SHA256,
            "sessions": len(sessions),
            "underlying_rows": int(len(underlying)),
            "raw_option_rows": int(len(options)),
        },
        "method": {
            "synthetic_forward_proxy": (
                "current futures-referenced ATM strike + ATM CE close - ATM PE close"
            ),
            "important_caveat": (
                "short-horizon call-put parity proxy only; no rate/dividend adjustment "
                "and not an exact theoretical forward level"
            ),
            "lead_gap_move_bps": (
                "current 5m synthetic-forward proxy return minus current 5m "
                "canonical futures return"
            ),
            "outcome": "subsequent canonical futures returns; signal bar excluded",
            "chronological_blocks": "8 x 10 development sessions",
            "strategy_logic": False,
            "threshold_optimization": False,
        },
        "qa": {
            "atm_pairs": int(len(pair)),
            "first_bar_rows_without_prior_return": int(
                frame["lead_gap_move_bps"].isna().sum()
            ),
            "scorable_next_5m_rows": int(len(valid)),
            "futures_source_values": sorted(
                str(value) for value in underlying["futures_source"].dropna().unique()
            ),
            "option_source_values": sorted(
                str(value) for value in options["source"].dropna().unique()
            ),
        },
        "pooled_next_5m": {
            "lead_gap_vs_next_5m_spearman": _spearman(
                valid, "lead_gap_move_bps", "future_ret_5m_bps"
            ),
            "mean_direction_aligned_next_5m_bps": float(
                valid["aligned_next_5m_bps"].mean()
            ),
            "median_direction_aligned_next_5m_bps": float(
                valid["aligned_next_5m_bps"].median()
            ),
            "direction_hit_rate": float((valid["aligned_next_5m_bps"] > 0).mean()),
            **pooled_ols,
        },
        "block_stability": blocks,
        "dte_stability": dte_summary,
        "magnitude_quartiles_development_only": quartiles,
        "cumulative_horizon_profile": horizon_profile,
        "individual_future_bar_profile": individual_bar_profile,
        "interpretation_guardrail": (
            "This atlas may justify further development-only diagnostics, but it "
            "does not freeze O3, does not establish tradability, and must not "
            "consume a blind block until timestamp/microstructure and "
            "incremental-information checks are completed."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build development-only NIFTY options lead/lag atlas"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    atlas = build_atlas(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(atlas, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "qa": atlas["qa"],
                "pooled_next_5m": atlas["pooled_next_5m"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
