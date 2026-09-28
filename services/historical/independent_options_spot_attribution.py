"""Development-only spot attribution for the NIFTY options lead/lag observation.

This module asks whether the ATM options-implied relative move contains
incremental information beyond contemporaneous NIFTY cash and futures moves.
It does not define or freeze O3 and never reads blind datasets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

OPTIONS_DEVELOPMENT_SHA256 = "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c"
SPOT_DEVELOPMENT_SHA256 = "372703608cc10643e8f6fdb0ea4c0c91ba8676c19c4d3fe6c38b596304aab0cb"


def _spearman(frame: pd.DataFrame, left: str, right: str) -> float:
    return float(frame[left].corr(frame[right], method="spearman"))


def _ols(frame: pd.DataFrame, outcome: str, features: list[str]) -> tuple[dict[str, float], float]:
    x = np.column_stack(
        [np.ones(len(frame))]
        + [frame[name].to_numpy(dtype=float) for name in features]
    )
    y = frame[outcome].to_numpy(dtype=float)
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    fitted = x @ beta
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    names = ["intercept"] + features
    return (
        {name: float(value) for name, value in zip(names, beta)},
        float(1.0 - ss_res / ss_tot) if ss_tot else 0.0,
    )


def _atm_strike(price: pd.Series) -> pd.Series:
    return (np.floor(price / 50.0 + 0.5) * 50.0).astype(int)


def build_attribution(options_payload: dict[str, Any], spot_payload: dict[str, Any]) -> dict[str, Any]:
    sessions = list(options_payload["session_dates"])
    if sessions != list(spot_payload["session_dates"]):
        raise ValueError("spot session dates do not exactly match options development sessions")
    if len(sessions) != 80:
        raise ValueError(f"expected 80 development sessions, got {len(sessions)}")

    futures = pd.DataFrame(options_payload["underlying_market_rows"]).copy()
    options = pd.DataFrame(options_payload["option_candles"]).copy()
    spot = pd.DataFrame(spot_payload["spot_rows"]).copy()
    for frame in (futures, options, spot):
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        frame["date"] = frame["timestamp"].dt.date.astype(str)

    futures = futures.sort_values("timestamp").reset_index(drop=True)
    block_by_date = {day: index // 10 + 1 for index, day in enumerate(sessions)}
    futures["block"] = futures["date"].map(block_by_date)
    futures["expiry"] = futures["date"].map(options_payload["contract_by_date"])
    futures["atm"] = _atm_strike(futures["futures_close"])

    wanted = futures[["timestamp", "date", "expiry", "atm"]]
    selected = options.merge(wanted, on=["timestamp", "date", "expiry"], how="inner")
    selected = selected[selected["strike"] == selected["atm"]].copy()
    pair = selected.pivot_table(
        index=["timestamp", "expiry", "strike"],
        columns="right",
        values="close",
        aggfunc="first",
    ).reset_index()
    if len(pair) != len(futures) or not {"CE", "PE"}.issubset(pair.columns):
        raise ValueError("ATM CE/PE pairing is incomplete")
    pair["synthetic_forward"] = pair["strike"] + pair["CE"] - pair["PE"]

    frame = futures[
        ["timestamp", "date", "block", "futures_close", "atm"]
    ].merge(
        pair[["timestamp", "synthetic_forward"]], on="timestamp", how="inner"
    ).merge(
        spot[["timestamp", "close"]].rename(columns={"close": "spot_close"}),
        on="timestamp",
        how="inner",
    )
    if len(frame) != len(futures):
        raise ValueError("spot/futures/options timestamps are not fully aligned")

    for name in ("futures_close", "synthetic_forward", "spot_close"):
        previous = frame.groupby("date")[name].shift(1)
        frame[f"{name}_ret_bps"] = (frame[name] / previous - 1.0) * 10000.0
    frame["next_futures_ret_bps"] = (
        frame.groupby("date")["futures_close"].shift(-1) / frame["futures_close"] - 1.0
    ) * 10000.0
    frame["options_gap_bps"] = (
        frame["synthetic_forward_ret_bps"] - frame["futures_close_ret_bps"]
    )
    frame["spot_gap_bps"] = frame["spot_close_ret_bps"] - frame["futures_close_ret_bps"]
    frame["previous_atm"] = frame.groupby("date")["atm"].shift(1)
    frame["atm_switched"] = frame["atm"] != frame["previous_atm"]

    valid = frame.dropna(
        subset=[
            "options_gap_bps",
            "spot_gap_bps",
            "futures_close_ret_bps",
            "next_futures_ret_bps",
        ]
    ).copy()

    opt_model, opt_r2 = _ols(
        valid, "next_futures_ret_bps", ["futures_close_ret_bps", "options_gap_bps"]
    )
    spot_model, spot_r2 = _ols(
        valid, "next_futures_ret_bps", ["futures_close_ret_bps", "spot_gap_bps"]
    )
    both_model, both_r2 = _ols(
        valid,
        "next_futures_ret_bps",
        ["futures_close_ret_bps", "spot_gap_bps", "options_gap_bps"],
    )

    block_rows = []
    for block, group in valid.groupby("block"):
        coefficients, r2 = _ols(
            group,
            "next_futures_ret_bps",
            ["futures_close_ret_bps", "spot_gap_bps", "options_gap_bps"],
        )
        _, spot_block_r2 = _ols(
            group, "next_futures_ret_bps", ["futures_close_ret_bps", "spot_gap_bps"]
        )
        block_rows.append(
            {
                "block": int(block),
                "rows": int(len(group)),
                "options_gap_spearman": _spearman(
                    group, "options_gap_bps", "next_futures_ret_bps"
                ),
                "spot_gap_spearman": _spearman(
                    group, "spot_gap_bps", "next_futures_ret_bps"
                ),
                "options_gap_coefficient_with_spot": coefficients["options_gap_bps"],
                "spot_gap_coefficient_with_options": coefficients["spot_gap_bps"],
                "incremental_r2_options_over_spot": float(r2 - spot_block_r2),
            }
        )

    # Leave-one-10-session-block-out residualization prevents the incremental
    # options component from being defined using its own evaluation block.
    valid["crossfit_options_residual"] = np.nan
    for block in sorted(valid["block"].unique()):
        train = valid[valid["block"] != block]
        test = valid[valid["block"] == block]
        x_train = np.column_stack(
            [
                np.ones(len(train)),
                train["spot_gap_bps"].to_numpy(dtype=float),
                train["futures_close_ret_bps"].to_numpy(dtype=float),
            ]
        )
        beta = np.linalg.lstsq(
            x_train, train["options_gap_bps"].to_numpy(dtype=float), rcond=None
        )[0]
        x_test = np.column_stack(
            [
                np.ones(len(test)),
                test["spot_gap_bps"].to_numpy(dtype=float),
                test["futures_close_ret_bps"].to_numpy(dtype=float),
            ]
        )
        valid.loc[test.index, "crossfit_options_residual"] = (
            test["options_gap_bps"].to_numpy(dtype=float) - x_test @ beta
        )
    valid["crossfit_aligned_next_bps"] = (
        np.sign(valid["crossfit_options_residual"]) * valid["next_futures_ret_bps"]
    )

    # Empirical bar-alignment challenge: same-bar synthetic movement should
    # dominate adjacent-bar alignment if timestamps are not shifted by one bar.
    alignment = []
    base = frame.dropna(
        subset=["synthetic_forward_ret_bps", "spot_close_ret_bps"]
    ).copy()
    for lag in (-1, 0, 1):
        base["target"] = base.groupby("date")["spot_close_ret_bps"].shift(-lag)
        sample = base.dropna(subset=["target"])
        alignment.append(
            {
                "spot_bar_lag": lag,
                "rows": int(len(sample)),
                "spearman": _spearman(
                    sample, "synthetic_forward_ret_bps", "target"
                ),
            }
        )

    stable_atm = valid[~valid["atm_switched"]]
    switched_atm = valid[valid["atm_switched"]]
    return {
        "research_type": "NIFTY_OPTIONS_SPOT_ATTRIBUTION_V1",
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "source": {
            "options_expected_sha256": OPTIONS_DEVELOPMENT_SHA256,
            "spot_expected_sha256": SPOT_DEVELOPMENT_SHA256,
            "sessions": len(sessions),
            "aligned_rows": int(len(frame)),
            "scorable_next_5m_rows": int(len(valid)),
        },
        "pooled": {
            "options_gap_vs_spot_gap_spearman": _spearman(
                valid, "options_gap_bps", "spot_gap_bps"
            ),
            "options_gap_vs_next_5m_spearman": _spearman(
                valid, "options_gap_bps", "next_futures_ret_bps"
            ),
            "spot_gap_vs_next_5m_spearman": _spearman(
                valid, "spot_gap_bps", "next_futures_ret_bps"
            ),
            "options_only_model": {"coefficients": opt_model, "r_squared": opt_r2},
            "spot_only_model": {"coefficients": spot_model, "r_squared": spot_r2},
            "combined_model": {"coefficients": both_model, "r_squared": both_r2},
            "incremental_r2_options_over_spot": float(both_r2 - spot_r2),
            "incremental_r2_spot_over_options": float(both_r2 - opt_r2),
        },
        "block_stability": block_rows,
        "crossfit_incremental_options_component": {
            "spearman_vs_next_5m": _spearman(
                valid, "crossfit_options_residual", "next_futures_ret_bps"
            ),
            "positive_spearman_blocks": int(
                sum(
                    _spearman(group, "crossfit_options_residual", "next_futures_ret_bps") > 0
                    for _, group in valid.groupby("block")
                )
            ),
            "mean_direction_aligned_next_5m_bps": float(
                valid["crossfit_aligned_next_bps"].mean()
            ),
            "median_direction_aligned_next_5m_bps": float(
                valid["crossfit_aligned_next_bps"].median()
            ),
            "direction_hit_rate": float((valid["crossfit_aligned_next_bps"] > 0).mean()),
            "positive_mean_blocks": int(
                sum(
                    group["crossfit_aligned_next_bps"].mean() > 0
                    for _, group in valid.groupby("block")
                )
            ),
        },
        "timestamp_alignment_challenge": alignment,
        "atm_switch_challenge": {
            "stable_atm_rows": int(len(stable_atm)),
            "stable_atm_spearman": _spearman(
                stable_atm, "options_gap_bps", "next_futures_ret_bps"
            ),
            "switched_atm_rows": int(len(switched_atm)),
            "switched_atm_spearman": _spearman(
                switched_atm, "options_gap_bps", "next_futures_ret_bps"
            ),
        },
        "interpretation_guardrail": (
            "Spot does not explain the development options-implied lead away, but "
            "this remains development-only. Complete microstructure/staleness and "
            "execution-feasibility diagnostics before freezing any O3 candidate."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Attribute development options lead against NIFTY spot")
    parser.add_argument("--options-input", type=Path, required=True)
    parser.add_argument("--spot-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    options_payload = json.loads(args.options_input.read_text(encoding="utf-8"))
    spot_payload = json.loads(args.spot_input.read_text(encoding="utf-8"))
    report = build_attribution(options_payload, spot_payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "pooled": report["pooled"]}, indent=2))


if __name__ == "__main__":
    main()
