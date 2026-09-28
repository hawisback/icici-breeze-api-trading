"""Frozen cross-cohort replication measurements for NIFTY Development Cohort 2.

Definitions in this module are committed before Cohort-2 data are collected.
The module compares Cohort 1 and Cohort 2 using identical calculations; it is
not a feature-search or threshold-optimization engine.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_cohort2_protocol import (
    PROTOCOL_VERSION,
    SESSION_DATES as COHORT2_SESSION_DATES,
    validate_auxiliary,
    validate_market,
    validate_options,
)

COHORT1_SESSIONS = 80
COHORT1_BLOCK_SIZE = 10
COHORT2_BLOCK_SIZE = 12
HORIZONS = (10, 15, 30, 60)


def _spearman(left: pd.Series, right: pd.Series) -> float:
    pair = pd.concat([left, right], axis=1).dropna()
    return float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="spearman"))


def _atm(price: pd.Series) -> pd.Series:
    return (np.floor(price / 50.0 + 0.5) * 50.0).astype(int)


def _rolling_sum(frame: pd.DataFrame, column: str, window: int) -> pd.Series:
    return (
        frame[column]
        .groupby(frame["date"])
        .rolling(window)
        .sum()
        .reset_index(level=0, drop=True)
    )


def _block_spearman(
    frame: pd.DataFrame, predictor: str, outcome: str
) -> list[float]:
    values = []
    for _, group in frame.dropna(subset=[predictor, outcome]).groupby("block"):
        values.append(_spearman(group[predictor], group[outcome]))
    return values


def _design_matrix(
    frame: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
) -> pd.DataFrame:
    parts = [frame[numeric].astype(float)] if numeric else []
    for name in categorical:
        dummies = pd.get_dummies(
            frame[name].astype(str), prefix=name, dtype=float
        )
        if dummies.shape[1] > 1:
            dummies = dummies.iloc[:, 1:]
        parts.append(dummies)
    return pd.concat(parts, axis=1) if parts else pd.DataFrame(index=frame.index)


def _crossfit_residual(
    frame: pd.DataFrame,
    target: str,
    *,
    numeric_controls: list[str],
    categorical_controls: list[str],
) -> pd.Series:
    required = [target, "block"] + numeric_controls + categorical_controls
    valid = frame.dropna(subset=required).copy()
    matrix = _design_matrix(valid, numeric_controls, categorical_controls)
    residual = pd.Series(np.nan, index=frame.index, dtype=float)

    for block in sorted(valid["block"].unique()):
        train_mask = valid["block"] != block
        test_mask = valid["block"] == block
        x_train = matrix.loc[train_mask].to_numpy(dtype=float)
        x_test = matrix.loc[test_mask].to_numpy(dtype=float)
        if x_train.shape[1]:
            mean = x_train.mean(axis=0)
            std = x_train.std(axis=0)
            std[std == 0.0] = 1.0
            x_train = (x_train - mean) / std
            x_test = (x_test - mean) / std
        x_train = np.column_stack([np.ones(len(x_train)), x_train])
        x_test = np.column_stack([np.ones(len(x_test)), x_test])
        beta = np.linalg.lstsq(
            x_train,
            valid.loc[train_mask, target].to_numpy(dtype=float),
            rcond=None,
        )[0]
        residual.loc[valid.index[test_mask]] = (
            valid.loc[test_mask, target].to_numpy(dtype=float) - x_test @ beta
        )
    return residual


def _partial_crossfit_spearman(
    frame: pd.DataFrame,
    predictor: str,
    outcome: str,
    *,
    numeric_controls: list[str],
    categorical_controls: list[str],
) -> tuple[float, list[float], int]:
    predictor_residual = _crossfit_residual(
        frame,
        predictor,
        numeric_controls=numeric_controls,
        categorical_controls=categorical_controls,
    )
    outcome_residual = _crossfit_residual(
        frame,
        outcome,
        numeric_controls=numeric_controls,
        categorical_controls=categorical_controls,
    )
    temp = frame[["block"]].copy()
    temp["predictor_residual"] = predictor_residual
    temp["outcome_residual"] = outcome_residual
    temp = temp.dropna()
    blocks = _block_spearman(temp, "predictor_residual", "outcome_residual")
    return (
        _spearman(temp["predictor_residual"], temp["outcome_residual"]),
        blocks,
        int(len(temp)),
    )


def _expiry_from_instrument(value: str) -> pd.Timestamp:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(value))
    if not match:
        raise ValueError(f"cannot parse futures expiry from {value!r}")
    return pd.Timestamp(match.group(1))


def build_frame(
    market: dict[str, Any],
    vix: dict[str, Any],
    options: dict[str, Any],
    spot: dict[str, Any],
    *,
    block_size: int,
) -> pd.DataFrame:
    sessions = list(market["session_dates"])
    if sessions != list(vix["session_dates"]):
        raise ValueError("VIX sessions do not exactly match market sessions")
    if sessions != list(options["session_dates"]):
        raise ValueError("options sessions do not exactly match market sessions")
    if sessions != list(spot["session_dates"]):
        raise ValueError("spot sessions do not exactly match market sessions")
    if len(sessions) % block_size:
        raise ValueError("session count must divide evenly into frozen block size")

    frame = pd.DataFrame(market["canonical_market_rows"]).copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["date"] = frame["timestamp"].dt.date.astype(str)
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame["block"] = frame["date"].map(
        {day: index // block_size + 1 for index, day in enumerate(sessions)}
    )
    group = frame.groupby("date")
    frame["previous_close"] = group["futures_close"].shift(1)
    frame["futures_return_bps"] = (
        frame["futures_close"] / frame["previous_close"] - 1.0
    ) * 10000.0
    frame["current_abs_return_bps"] = frame["futures_return_bps"].abs()
    previous_oi = group["futures_open_interest"].shift(1)
    frame["oi_change_bps"] = (
        frame["futures_open_interest"] / previous_oi - 1.0
    ) * 10000.0
    frame["abs_oi_change_bps"] = frame["oi_change_bps"].abs()
    frame["log_futures_volume"] = np.log(
        frame["futures_volume"].replace(0.0, np.nan)
    )

    for window in (3, 6):
        start_open = group["futures_open"].shift(window - 1)
        highs = pd.concat(
            [group["futures_high"].shift(lag) for lag in range(window)], axis=1
        ).max(axis=1)
        lows = pd.concat(
            [group["futures_low"].shift(lag) for lag in range(window)], axis=1
        ).min(axis=1)
        frame[f"range_{window}_bps"] = (
            (highs - lows) / start_open * 10000.0
        )
        frame[f"close_location_{window}"] = (
            2.0
            * (frame["futures_close"] - lows)
            / (highs - lows).replace(0.0, np.nan)
            - 1.0
        )
        frame[f"net_return_{window}_bps"] = (
            frame["futures_close"] / start_open - 1.0
        ) * 10000.0
        frame[f"path_length_{window}_bps"] = _rolling_sum(
            frame.assign(_abs=frame["futures_return_bps"].abs()), "_abs", window
        )

    next_open = group["futures_open"].shift(-1)
    for minutes in HORIZONS:
        bars = minutes // 5
        end_close = group["futures_close"].shift(-bars)
        frame[f"future_terminal_{minutes}m_bps"] = (
            end_close / next_open - 1.0
        ) * 10000.0
        highs = pd.concat(
            [group["futures_high"].shift(-lag) for lag in range(1, bars + 1)],
            axis=1,
        ).max(axis=1)
        lows = pd.concat(
            [group["futures_low"].shift(-lag) for lag in range(1, bars + 1)],
            axis=1,
        ).min(axis=1)
        frame[f"future_max_excursion_{minutes}m_bps"] = np.maximum(
            (highs / next_open - 1.0).abs(),
            (lows / next_open - 1.0).abs(),
        ) * 10000.0

    frame["next_close_return_bps"] = (
        group["futures_close"].shift(-1) / frame["futures_close"] - 1.0
    ) * 10000.0

    frame["futures_expiry"] = frame["futures_instrument"].map(
        _expiry_from_instrument
    )
    session_date = pd.to_datetime(frame["date"])
    frame["futures_dte"] = (
        frame["futures_expiry"] - session_date
    ).dt.days
    minutes = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    frame["time_bucket_30m"] = ((minutes - (9 * 60 + 15)) // 30).astype(int)

    vix_frame = pd.DataFrame(vix["vix_rows"]).copy()
    vix_frame["timestamp"] = pd.to_datetime(vix_frame["timestamp"])
    vix_frame = vix_frame[["timestamp", "close"]].rename(
        columns={"close": "vix_close"}
    )
    # canonical_market_rows may carry null VIX placeholders; the standalone\n    # VIX corpus is authoritative for this frozen replication. Drop the\n    # placeholder before merging so pandas does not suffix vix_close.\n    frame = frame.drop(columns=["vix_close"], errors="ignore")\n    frame = frame.merge(vix_frame, on="timestamp", how="left", validate="one_to_one")
    frame["vix_5m_change_bps"] = (
        frame["vix_close"]
        / frame.groupby("date")["vix_close"].shift(1)
        - 1.0
    ) * 10000.0
    frame["vix_15m_change_bps"] = (
        frame["vix_close"]
        / frame.groupby("date")["vix_close"].shift(3)
        - 1.0
    ) * 10000.0

    spot_frame = pd.DataFrame(spot["spot_rows"]).copy()
    spot_frame["timestamp"] = pd.to_datetime(spot_frame["timestamp"])
    spot_frame = spot_frame[["timestamp", "close"]].rename(
        columns={"close": "spot_close"}
    )
    # Likewise, standalone spot is authoritative over null market placeholders.\n    frame = frame.drop(columns=["spot_close"], errors="ignore")\n    frame = frame.merge(spot_frame, on="timestamp", how="left", validate="one_to_one")
    frame["spot_return_bps"] = (
        frame["spot_close"]
        / frame.groupby("date")["spot_close"].shift(1)
        - 1.0
    ) * 10000.0
    frame["spot_gap_bps"] = frame["spot_return_bps"] - frame["futures_return_bps"]

    frame["option_expiry"] = frame["date"].map(options["contract_by_date"])
    frame["atm"] = _atm(frame["futures_close"])
    option_frame = pd.DataFrame(options["option_candles"]).copy()
    option_frame["timestamp"] = pd.to_datetime(option_frame["timestamp"])
    selected = option_frame.merge(
        frame[["timestamp", "option_expiry", "atm"]],
        left_on=["timestamp", "expiry", "strike"],
        right_on=["timestamp", "option_expiry", "atm"],
        how="inner",
    )
    pair = selected.pivot_table(
        index=["timestamp", "expiry", "strike"],
        columns="right",
        values="close",
        aggfunc="first",
    ).reset_index()
    if not {"CE", "PE"}.issubset(pair.columns):
        raise ValueError("ATM CE/PE pair is incomplete")
    pair["synthetic_forward"] = pair["strike"] + pair["CE"] - pair["PE"]
    pair["atm_straddle"] = pair["CE"] + pair["PE"]
    frame = frame.merge(
        pair[["timestamp", "synthetic_forward", "atm_straddle"]],
        on="timestamp",
        how="left",
        validate="one_to_one",
    )
    if frame[["synthetic_forward", "atm_straddle"]].isna().any().any():
        raise ValueError("ATM option pairing is incomplete after merge")
    frame["straddle_fraction"] = (
        frame["atm_straddle"] / frame["futures_close"]
    )
    frame["synthetic_forward_return_bps"] = (
        frame["synthetic_forward"]
        / frame.groupby("date")["synthetic_forward"].shift(1)
        - 1.0
    ) * 10000.0
    frame["options_gap_bps"] = (
        frame["synthetic_forward_return_bps"] - frame["futures_return_bps"]
    )
    option_expiry = pd.to_datetime(frame["option_expiry"])
    frame["option_dte"] = (option_expiry - session_date).dt.days
    frame["option_dte_time_bucket"] = (
        frame["option_dte"].astype("Int64").astype(str)
        + "_"
        + frame["time_bucket_30m"].astype(str)
    )
    return frame


def _metric(
    frame: pd.DataFrame, predictor: str, outcome: str
) -> dict[str, Any]:
    blocks = _block_spearman(frame, predictor, outcome)
    sample = frame.dropna(subset=[predictor, outcome])
    return {
        "rows": int(len(sample)),
        "spearman": _spearman(sample[predictor], sample[outcome]),
        "block_spearman": blocks,
        "positive_blocks": int(sum(value > 0 for value in blocks)),
        "negative_blocks": int(sum(value < 0 for value in blocks)),
        "total_blocks": len(blocks),
    }


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    futures_controls_numeric = [
        "current_abs_return_bps",
        "log_futures_volume",
        "abs_oi_change_bps",
    ]
    futures_controls_categorical = ["time_bucket_30m", "futures_dte"]

    vix_partial, vix_blocks, vix_rows = _partial_crossfit_spearman(
        frame,
        "vix_close",
        "future_max_excursion_30m_bps",
        numeric_controls=["range_3_bps"] + futures_controls_numeric,
        categorical_controls=futures_controls_categorical,
    )

    straddle_partial, straddle_blocks, straddle_rows = _partial_crossfit_spearman(
        frame,
        "straddle_fraction",
        "future_max_excursion_30m_bps",
        numeric_controls=[
            "range_3_bps",
            "vix_close",
            "vix_5m_change_bps",
            "vix_15m_change_bps",
        ]
        + futures_controls_numeric,
        categorical_controls=["option_dte_time_bucket", "futures_dte"],
    )

    lead_valid = frame.dropna(
        subset=[
            "options_gap_bps",
            "spot_gap_bps",
            "futures_return_bps",
            "next_close_return_bps",
        ]
    ).copy()
    lead_valid["crossfit_options_residual"] = _crossfit_residual(
        lead_valid,
        "options_gap_bps",
        numeric_controls=["spot_gap_bps", "futures_return_bps"],
        categorical_controls=[],
    )
    lead_valid["crossfit_aligned_next_bps"] = (
        np.sign(lead_valid["crossfit_options_residual"])
        * lead_valid["next_close_return_bps"]
    )
    lead_blocks = _block_spearman(
        lead_valid, "crossfit_options_residual", "next_close_return_bps"
    )

    return {
        "sessions": int(frame["date"].nunique()),
        "rows": int(len(frame)),
        "blocks": int(frame["block"].nunique()),
        "futures_sequence": {
            "range_3_vs_future_30m_max_excursion": _metric(
                frame, "range_3_bps", "future_max_excursion_30m_bps"
            ),
            "range_6_vs_future_30m_max_excursion": _metric(
                frame, "range_6_bps", "future_max_excursion_30m_bps"
            ),
            "close_location_3_vs_future_10m_terminal": _metric(
                frame, "close_location_3", "future_terminal_10m_bps"
            ),
        },
        "vix_incremental": {
            "definition": (
                "leave-one-block-out partial Spearman: VIX level and future 30m "
                "max excursion are each residualized against 3-bar futures range, "
                "current absolute futures return, log futures volume, absolute OI "
                "change, 30m time bucket and futures DTE"
            ),
            "rows": vix_rows,
            "spearman": vix_partial,
            "block_spearman": vix_blocks,
            "positive_blocks": int(sum(value > 0 for value in vix_blocks)),
            "negative_blocks": int(sum(value < 0 for value in vix_blocks)),
        },
        "atm_straddle_incremental": {
            "definition": (
                "leave-one-block-out partial Spearman after 3-bar futures range, "
                "VIX level/5m/15m change, current futures movement/volume/OI, exact "
                "option-DTE x 30m bucket, and futures DTE"
            ),
            "rows": straddle_rows,
            "spearman": straddle_partial,
            "block_spearman": straddle_blocks,
            "positive_blocks": int(sum(value > 0 for value in straddle_blocks)),
            "negative_blocks": int(sum(value < 0 for value in straddle_blocks)),
        },
        "options_fast_lead": {
            "raw": _metric(frame, "options_gap_bps", "next_close_return_bps"),
            "spot_attributed_crossfit": {
                "rows": int(len(lead_valid)),
                "spearman": _spearman(
                    lead_valid["crossfit_options_residual"],
                    lead_valid["next_close_return_bps"],
                ),
                "block_spearman": lead_blocks,
                "positive_blocks": int(sum(value > 0 for value in lead_blocks)),
                "negative_blocks": int(sum(value < 0 for value in lead_blocks)),
                "mean_direction_aligned_next_5m_bps": float(
                    lead_valid["crossfit_aligned_next_bps"].mean()
                ),
                "direction_hit_rate": float(
                    (lead_valid["crossfit_aligned_next_bps"] > 0).mean()
                ),
            },
            "intrabar_stage_gate": (
                "If both raw and spot-attributed lead have the Cohort-1 sign and "
                "at least 5/6 Cohort-2 blocks share that sign, run the separately "
                "frozen one-minute timing replication. Otherwise do not rescue."
            ),
        },
    }


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _replication_check(
    cohort1: dict[str, Any],
    cohort2: dict[str, Any],
    path: list[str],
) -> dict[str, Any]:
    left: Any = cohort1
    right: Any = cohort2
    for key in path:
        left = left[key]
        right = right[key]
    c1_sign = _sign(float(left["spearman"]))
    c2_sign = _sign(float(right["spearman"]))
    if c1_sign > 0:
        same_sign_blocks = int(right.get("positive_blocks", 0))
    elif c1_sign < 0:
        same_sign_blocks = int(right.get("negative_blocks", 0))
    else:
        same_sign_blocks = 0
    return {
        "metric": ".".join(path),
        "cohort1_spearman": float(left["spearman"]),
        "cohort2_spearman": float(right["spearman"]),
        "cohort2_to_cohort1_abs_effect_ratio": (
            abs(float(right["spearman"])) / abs(float(left["spearman"]))
            if float(left["spearman"]) != 0.0
            else None
        ),
        "same_pooled_sign": c1_sign != 0 and c1_sign == c2_sign,
        "cohort2_same_sign_blocks": same_sign_blocks,
        "cohort2_total_blocks": int(right.get("total_blocks", 6)),
        "directional_replication": (
            c1_sign != 0 and c1_sign == c2_sign and same_sign_blocks >= 5
        ),
    }


def build_replication_report(
    cohort1_inputs: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
    cohort2_inputs: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
) -> dict[str, Any]:
    c1_market, c1_vix, c1_options, c1_spot = cohort1_inputs
    c2_market, c2_vix, c2_options, c2_spot = cohort2_inputs

    validate_market(c2_market)
    validate_options(c2_options)
    validate_auxiliary(c2_vix, "vix")
    validate_auxiliary(c2_spot, "spot")

    c1_frame = build_frame(
        c1_market, c1_vix, c1_options, c1_spot, block_size=COHORT1_BLOCK_SIZE
    )
    if c1_frame["date"].nunique() != COHORT1_SESSIONS:
        raise ValueError("Cohort 1 must contain exactly 80 sessions")
    c2_frame = build_frame(
        c2_market, c2_vix, c2_options, c2_spot, block_size=COHORT2_BLOCK_SIZE
    )
    if list(c2_market["session_dates"]) != COHORT2_SESSION_DATES:
        raise ValueError("Cohort 2 session dates changed after protocol freeze")

    c1 = summarize(c1_frame)
    c2 = summarize(c2_frame)
    checks = [
        _replication_check(
            c1, c2,
            ["futures_sequence", "range_3_vs_future_30m_max_excursion"],
        ),
        _replication_check(
            c1, c2,
            ["futures_sequence", "range_6_vs_future_30m_max_excursion"],
        ),
        _replication_check(
            c1, c2,
            ["futures_sequence", "close_location_3_vs_future_10m_terminal"],
        ),
        _replication_check(c1, c2, ["vix_incremental"]),
        _replication_check(c1, c2, ["atm_straddle_incremental"]),
        _replication_check(c1, c2, ["options_fast_lead", "raw"]),
        _replication_check(
            c1, c2, ["options_fast_lead", "spot_attributed_crossfit"]
        ),
    ]
    return {
        "research_type": "NIFTY_DEVELOPMENT_COHORT_2_REPLICATION_V1",
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "threshold_optimization": False,
        "cohort1": c1,
        "cohort2": c2,
        "replication_checks": checks,
        "decision_guardrail": (
            "Replication labels describe structural repeatability only. They do "
            "not promote a trading strategy. Failed relationships are recorded "
            "without rescue filters; successful movement-state replication does "
            "not restart paused target-sizing work."
        ),
    }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen Cohort-2 replication report")
    for cohort in ("cohort1", "cohort2"):
        for kind in ("market", "vix", "options", "spot"):
            parser.add_argument(f"--{cohort}-{kind}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    c1 = tuple(_load(getattr(args, f"cohort1_{kind}")) for kind in ("market", "vix", "options", "spot"))
    c2 = tuple(_load(getattr(args, f"cohort2_{kind}")) for kind in ("market", "vix", "options", "spot"))
    report = build_replication_report(c1, c2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "protocol_version": report["protocol_version"],
        "replication_checks": report["replication_checks"],
    }, indent=2))


if __name__ == "__main__":
    main()
